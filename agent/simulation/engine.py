import math
import shutil
from pathlib import Path

from agent.simulation.route_loader import load_route
from agent.simulation.state import disarm_simulation

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    aa = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(aa), math.sqrt(1 - aa))
    return r * c


class SimulationEngine:
    def __init__(self, sim_state: dict):
        scenario_id = str(sim_state.get("scenario_id") or "").strip()
        if not scenario_id:
            raise ValueError("simulation scenario_id missing")

        self.route = load_route(scenario_id)
        self.sim_state = sim_state

        self.route_id = self.route["id"]
        self.profile_type = self.route.get("profile_type")
        self.duration_s = float(self.route.get("duration_s") or 600)
        self.waypoints = self.route.get("waypoints") or []
        self.zones = self.route.get("zones") or []
        self.base_environment = self.route.get("base_environment") or {}
        self.image_set = self.route.get("image_set")

        if len(self.waypoints) < 1:
            raise ValueError(f"Route {self.route_id} has no waypoints")

        self.temp_offset = float(sim_state.get("temp_offset") or 0.0)
        self.hum_offset = float(sim_state.get("hum_offset") or 0.0)
        self.press_offset = float(sim_state.get("press_offset") or 0.0)
        self.gas_offset = float(sim_state.get("gas_offset") or 0.0)

        self.temp_trend = float(sim_state.get("temp_trend") or 0.0)
        self.hum_trend = float(sim_state.get("hum_trend") or 0.0)
        self.press_trend = float(sim_state.get("press_trend") or 0.0)
        self.gas_trend = float(sim_state.get("gas_trend") or 0.0)

    def is_finished(self, elapsed_s: float) -> bool:
        return elapsed_s >= self.duration_s

    def _position_at(self, elapsed_s: float) -> dict:
        if len(self.waypoints) == 1:
            wp = self.waypoints[0]
            return {
                "lat": float(wp["lat"]),
                "lon": float(wp["lon"]),
                "alt_m": float(wp.get("alt_m") or 0.0),
            }

        progress = _clamp(elapsed_s / max(self.duration_s, 1.0), 0.0, 1.0)
        segment_count = len(self.waypoints) - 1

        scaled = progress * segment_count
        seg_idx = min(int(scaled), segment_count - 1)
        local_t = scaled - seg_idx

        a = self.waypoints[seg_idx]
        b = self.waypoints[seg_idx + 1]

        return {
            "lat": _lerp(float(a["lat"]), float(b["lat"]), local_t),
            "lon": _lerp(float(a["lon"]), float(b["lon"]), local_t),
            "alt_m": _lerp(float(a.get("alt_m") or 0.0), float(b.get("alt_m") or 0.0), local_t),
        }

    def _zone_effect(self, zone: dict, lat: float, lon: float) -> float:
        zlat = zone.get("lat")
        zlon = zone.get("lon")
        radius_m = float(zone.get("radius_m") or 0.0)
        strength = float(zone.get("strength") or 0.0)

        if zlat is None or zlon is None or radius_m <= 0:
            return 0.0

        d = _distance_m(lat, lon, float(zlat), float(zlon))
        if d >= radius_m:
            return 0.0

        x = 1.0 - (d / radius_m)
        return strength * x

    def _environment_at(self, elapsed_s: float, lat: float, lon: float, alt_m: float) -> dict:
        base_temp = float(self.base_environment.get("temp_c") or 20.0)
        base_hum = float(self.base_environment.get("hum_pct") or 55.0)
        base_press = float(self.base_environment.get("press_hpa") or 1013.0)
        base_gas = float(self.base_environment.get("gas_ohms") or 12000.0)

        temp_local = 0.0
        hum_local = 0.0
        press_local = 0.0
        gas_local = 0.0

        for zone in self.zones:
            effect = self._zone_effect(zone, lat, lon)
            ztype = str(zone.get("type") or "").strip()

            if ztype == "temperature_patch":
                temp_local += 2.0 * effect
            elif ztype == "humidity_patch":
                hum_local += 5.0 * effect
            elif ztype == "pressure_patch":
                press_local += 1.2 * effect
            elif ztype == "gas_patch":
                gas_local += 1500.0 * effect
            elif ztype == "anomaly_zone":
                temp_local += 1.2 * effect
                gas_local += 2200.0 * effect
            elif ztype == "mixed_zone":
                temp_local += 0.6 * effect
                hum_local -= 2.0 * effect
                gas_local += 600.0 * effect

        # mic efect ideal de altitudine pentru presiune
        press_alt = -0.12 * float(alt_m)

        # trend per minute, ca să fie mai natural
        minutes = elapsed_s / 60.0

        temp = base_temp + temp_local + self.temp_offset + minutes * self.temp_trend
        hum = base_hum + hum_local + self.hum_offset + minutes * self.hum_trend
        press = base_press + press_local + press_alt + self.press_offset + minutes * self.press_trend
        gas = base_gas + gas_local + self.gas_offset + minutes * self.gas_trend

        hum = _clamp(hum, 0.0, 100.0)

        return {
            "temp_c": round(temp, 3),
            "hum_pct": round(hum, 3),
            "press_hpa": round(press, 3),
            "gas_ohms": round(gas, 3),
        }

    def _choose_image_category(self, lat: float, lon: float) -> str:
        # default
        category = "healthy"

        for zone in self.zones:
            effect = self._zone_effect(zone, lat, lon)
            if effect <= 0:
                continue

            ztype = str(zone.get("type") or "").strip()
            if ztype in ("anomaly_zone", "gas_patch"):
                return "unhealthy"
            if ztype in ("mixed_zone", "temperature_patch"):
                category = "mixed"

        return category

    def _copy_image_for_drone(self, output_path: str, elapsed_s: float, lat: float, lon: float) -> bool:
        category = self._choose_image_category(lat, lon)
        folder = ASSETS_DIR / "drone" / category
        if not folder.exists():
            return False

        files = sorted([p for p in folder.iterdir() if p.is_file()])
        if not files:
            return False

        idx = int(elapsed_s) % len(files)
        src = files[idx]

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)
        return True

    def capture_image(self, output_path: str, elapsed_s: float, lat: float, lon: float) -> bool:
        if self.profile_type != "drone":
            return False
        return self._copy_image_for_drone(output_path, elapsed_s, lat, lon)

    def sample(self, elapsed_s: float) -> dict:
        pos = self._position_at(elapsed_s)
        env = self._environment_at(
            elapsed_s=elapsed_s,
            lat=pos["lat"],
            lon=pos["lon"],
            alt_m=pos["alt_m"],
        )

        # GPS ideal
        return {
            "lat": pos["lat"],
            "lon": pos["lon"],
            "alt_m": pos["alt_m"],
            "fix_quality": 1,
            "satellites": 12,
            "hdop": 0.8,
            **env,
        }

    def build_meta_block(self) -> dict:
        return {
            "enabled": True,
            "route_id": self.route_id,
            "profile_type": self.profile_type,
            "ideal_sensor_model": True,
            "offsets": {
                "temp_offset": self.temp_offset,
                "hum_offset": self.hum_offset,
                "press_offset": self.press_offset,
                "gas_offset": self.gas_offset,
            },
            "trends": {
                "temp_trend": self.temp_trend,
                "hum_trend": self.hum_trend,
                "press_trend": self.press_trend,
                "gas_trend": self.gas_trend,
            },
        }

    @classmethod
    def from_state(cls, sim_state: dict) -> "SimulationEngine":
        return cls(sim_state)

    def finalize(self) -> None:
        # dezarmează simulatorul după consumarea unei misiuni
        disarm_simulation()
        