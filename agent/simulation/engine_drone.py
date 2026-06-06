import hashlib
import math
import random
import shutil
from pathlib import Path

from agent.simulation.sim_utils import (
    build_route_distance_table,
    clamp,
    distance_m,
    interpolate_route_by_distance,
    meters_to_lat_lon,
    to_float,
    zone_influence,
)

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def _stable_seed(*parts) -> int:
    text = "|".join(str(p) for p in parts)
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(h[:12], 16)


class DroneSimulationEngine:
    """
    Realistic-ish drone simulation engine.

    Main ideas:
    - route progress is distance-based, not waypoint-index-based;
    - drone speed has small smooth variations;
    - GPS has small horizontal/vertical jitter;
    - altitude can be AGL over terrain;
    - BME680 values have drift, noise, zone effects and sensor lag;
    - simulated images are selected based on local anomaly zones.
    """

    def __init__(self, route: dict, sim_state: dict):
        self.route = route
        self.sim_state = sim_state

        self.route_id = route["id"]
        self.profile_type = route.get("profile_type") or "drone"

        self.waypoints = route.get("waypoints") or []
        self.zones = route.get("zones") or []
        self.base_environment = route.get("base_environment") or {}
        self.image_set = route.get("image_set")

        if not self.waypoints:
            raise ValueError(f"Route {self.route_id} has no waypoints")

        self.motion_model = route.get("motion_model") or {}
        self.altitude_model = route.get("altitude_model") or {}
        self.gps_model = route.get("gps_model") or {}
        self.sensor_model = route.get("sensor_model") or {}
        self.timing_model = route.get("timing_model") or {}

        self.cumulative_distances, self.route_distance_m = build_route_distance_table(
            self.waypoints
        )

        self.nominal_duration_s = float(route.get("duration_s") or 600.0)

        seed = (
            self.motion_model.get("seed")
            or self.timing_model.get("seed")
            or sim_state.get("selected_at_epoch")
            or self.route_id
        )

        self.seed = _stable_seed(self.route_id, seed)
        self.rng = random.Random(self.seed)

        duration_variation_pct = to_float(
            self.motion_model.get(
                "duration_variation_pct",
                self.timing_model.get("duration_variation_pct", 0.06),
            ),
            0.06,
        )

        duration_factor = 1.0 + self.rng.uniform(
            -abs(duration_variation_pct),
            abs(duration_variation_pct),
        )

        self.duration_s = max(1.0, self.nominal_duration_s * duration_factor)

        self.temp_offset = to_float(sim_state.get("temp_offset"), 0.0)
        self.hum_offset = to_float(sim_state.get("hum_offset"), 0.0)
        self.press_offset = to_float(sim_state.get("press_offset"), 0.0)
        self.gas_offset = to_float(sim_state.get("gas_offset"), 0.0)

        self.temp_trend = to_float(sim_state.get("temp_trend"), 0.0)
        self.hum_trend = to_float(sim_state.get("hum_trend"), 0.0)
        self.press_trend = to_float(sim_state.get("press_trend"), 0.0)
        self.gas_trend = to_float(sim_state.get("gas_trend"), 0.0)

        self._phase_speed_1 = self.rng.uniform(0.0, math.tau)
        self._phase_speed_2 = self.rng.uniform(0.0, math.tau)
        self._phase_gps_n = self.rng.uniform(0.0, math.tau)
        self._phase_gps_e = self.rng.uniform(0.0, math.tau)
        self._phase_alt = self.rng.uniform(0.0, math.tau)
        self._phase_temp = self.rng.uniform(0.0, math.tau)
        self._phase_hum = self.rng.uniform(0.0, math.tau)
        self._phase_press = self.rng.uniform(0.0, math.tau)
        self._phase_gas = self.rng.uniform(0.0, math.tau)

        self._last_elapsed_s = None
        self._last_env = None

    def is_finished(self, elapsed_s: float) -> bool:
        return elapsed_s >= self.duration_s

    def _integrated_speed_wave(
        self,
        p: float,
        amplitude: float,
        cycles: float,
        phase: float,
    ) -> float:
        if abs(amplitude) <= 0.000001 or cycles <= 0:
            return p

        return p + amplitude / (math.tau * cycles) * (
            math.cos(phase) - math.cos(math.tau * cycles * p + phase)
        )

    def _motion_progress(self, elapsed_s: float) -> float:
        p = clamp(elapsed_s / max(self.duration_s, 1.0), 0.0, 1.0)

        speed_variation_pct = clamp(
            abs(to_float(self.motion_model.get("speed_variation_pct"), 0.06)),
            0.0,
            0.25,
        )

        # Smooth and monotonic speed variation.
        # Derivative is approximately 1 + small sinusoidal terms.
        a1 = speed_variation_pct
        a2 = speed_variation_pct * 0.45

        cycles1 = 2.0
        cycles2 = 5.0

        value = self._integrated_speed_wave(p, a1, cycles1, self._phase_speed_1)
        value = self._integrated_speed_wave(value, a2, cycles2, self._phase_speed_2)

        end = self._integrated_speed_wave(1.0, a1, cycles1, self._phase_speed_1)
        end = self._integrated_speed_wave(end, a2, cycles2, self._phase_speed_2)

        if abs(end) < 0.000001:
            return p

        return clamp(value / end, 0.0, 1.0)

    def _raw_position_at(self, elapsed_s: float) -> dict:
        progress = self._motion_progress(elapsed_s)
        target_distance = progress * self.route_distance_m

        return interpolate_route_by_distance(
            self.waypoints,
            self.cumulative_distances,
            target_distance,
        )

    def _altitude_at(self, raw_pos: dict, elapsed_s: float) -> float:
        mode = str(self.altitude_model.get("mode") or "auto").strip().lower()

        raw_alt = to_float(raw_pos.get("alt_m"), 0.0)
        ground_alt = raw_pos.get("ground_alt_m")

        default_ground_alt = to_float(
            self.altitude_model.get("default_ground_alt_m"),
            400.0,
        )

        flight_agl = self.altitude_model.get("flight_agl_m")
        vertical_variation_m = to_float(
            self.altitude_model.get("vertical_variation_m"),
            1.2,
        )

        if mode == "gps":
            base_alt = raw_alt

        elif mode == "agl_over_ground":
            ground = to_float(ground_alt, default_ground_alt)
            agl = to_float(flight_agl, raw_alt if raw_alt > 0 else 40.0)
            base_alt = ground + agl

        else:
            # Auto mode:
            # - if waypoint altitude is small, treat it as AGL;
            # - if waypoint altitude is large, treat it as GPS/sea-level altitude.
            if ground_alt is not None or raw_alt <= 150.0:
                ground = to_float(ground_alt, default_ground_alt)
                agl = to_float(flight_agl, raw_alt if raw_alt > 0 else 40.0)
                base_alt = ground + agl
            else:
                base_alt = raw_alt

        vertical_motion = vertical_variation_m * math.sin(
            0.035 * elapsed_s + self._phase_alt
        )

        return base_alt + vertical_motion

    def _gps_at(self, raw_pos: dict, alt_m: float, elapsed_s: float) -> dict:
        lat = raw_pos["lat"]
        lon = raw_pos["lon"]

        horizontal_noise_m = abs(
            to_float(self.gps_model.get("horizontal_noise_m"), 1.2)
        )
        vertical_noise_m = abs(
            to_float(self.gps_model.get("vertical_noise_m"), 1.8)
        )

        # Smooth GPS jitter, small enough to look real but not destroy the track.
        north_jitter = horizontal_noise_m * (
            0.65 * math.sin(0.75 * elapsed_s + self._phase_gps_n)
            + 0.35 * math.sin(0.19 * elapsed_s + self._phase_gps_n * 0.7)
        )

        east_jitter = horizontal_noise_m * (
            0.65 * math.sin(0.71 * elapsed_s + self._phase_gps_e)
            + 0.35 * math.sin(0.23 * elapsed_s + self._phase_gps_e * 0.6)
        )

        dlat, dlon = meters_to_lat_lon(lat, north_jitter, east_jitter)

        alt_noise = vertical_noise_m * math.sin(
            0.53 * elapsed_s + self._phase_alt * 1.7
        )

        satellites_min = int(to_float(self.gps_model.get("satellites_min"), 9))
        satellites_max = int(to_float(self.gps_model.get("satellites_max"), 14))

        if satellites_max < satellites_min:
            satellites_max = satellites_min

        sat_wave = 0.5 + 0.5 * math.sin(0.045 * elapsed_s + self._phase_gps_n)
        satellites = int(round(satellites_min + sat_wave * (satellites_max - satellites_min)))

        hdop_min = to_float(self.gps_model.get("hdop_min"), 0.7)
        hdop_max = to_float(self.gps_model.get("hdop_max"), 1.4)

        hdop_wave = 0.5 + 0.5 * math.sin(0.038 * elapsed_s + self._phase_gps_e)
        hdop = hdop_min + hdop_wave * (hdop_max - hdop_min)

        return {
            "lat": round(lat + dlat, 7),
            "lon": round(lon + dlon, 7),
            "alt_m": round(alt_m + alt_noise, 3),
            "fix_quality": 1,
            "satellites": satellites,
            "hdop": round(hdop, 2),
        }

    def _zone_effects_at(self, lat: float, lon: float) -> dict:
        effects = {
            "temp_c": 0.0,
            "hum_pct": 0.0,
            "press_hpa": 0.0,
            "gas_ohms": 0.0,
        }

        for zone in self.zones:
            influence = zone_influence(zone, lat, lon)
            if influence <= 0:
                continue

            explicit = zone.get("effects")
            if isinstance(explicit, dict):
                effects["temp_c"] += to_float(explicit.get("temp_c"), 0.0) * influence
                effects["hum_pct"] += to_float(explicit.get("hum_pct"), 0.0) * influence
                effects["press_hpa"] += to_float(explicit.get("press_hpa"), 0.0) * influence
                effects["gas_ohms"] += to_float(explicit.get("gas_ohms"), 0.0) * influence
                continue

            ztype = str(zone.get("type") or "").strip().lower()

            if ztype == "temperature_patch":
                effects["temp_c"] += 1.6 * influence
                effects["hum_pct"] -= 1.2 * influence
                effects["gas_ohms"] -= 250.0 * influence

            elif ztype == "humidity_patch":
                effects["hum_pct"] += 4.0 * influence
                effects["temp_c"] -= 0.3 * influence

            elif ztype == "pressure_patch":
                effects["press_hpa"] += 0.7 * influence

            elif ztype == "gas_patch":
                effects["gas_ohms"] -= 1800.0 * influence
                effects["temp_c"] += 0.25 * influence
                effects["hum_pct"] -= 0.8 * influence

            elif ztype in ("anomaly_zone", "vegetation_stress"):
                effects["temp_c"] += 1.3 * influence
                effects["hum_pct"] -= 3.2 * influence
                effects["gas_ohms"] -= 1000.0 * influence

            elif ztype == "mixed_zone":
                effects["temp_c"] += 0.65 * influence
                effects["hum_pct"] -= 1.6 * influence
                effects["gas_ohms"] -= 450.0 * influence

        return effects

    def _smooth_noise(self, elapsed_s: float, amplitude: float, phase: float, speed: float) -> float:
        return amplitude * (
            0.65 * math.sin(speed * elapsed_s + phase)
            + 0.35 * math.sin(speed * 0.37 * elapsed_s + phase * 1.9)
        )

    def _environment_target_at(self, elapsed_s: float, lat: float, lon: float, alt_m: float) -> dict:
        base_temp = to_float(self.base_environment.get("temp_c"), 22.0)
        base_hum = to_float(self.base_environment.get("hum_pct"), 55.0)
        base_press = to_float(self.base_environment.get("press_hpa"), 1013.0)
        base_gas = to_float(self.base_environment.get("gas_ohms"), 14000.0)

        zone_effects = self._zone_effects_at(lat, lon)

        minutes = elapsed_s / 60.0

        noise_cfg = self.sensor_model.get("noise") or {}

        temp_noise = self._smooth_noise(
            elapsed_s,
            to_float(noise_cfg.get("temp_c"), 0.08),
            self._phase_temp,
            0.18,
        )

        hum_noise = self._smooth_noise(
            elapsed_s,
            to_float(noise_cfg.get("hum_pct"), 0.45),
            self._phase_hum,
            0.15,
        )

        press_noise = self._smooth_noise(
            elapsed_s,
            to_float(noise_cfg.get("press_hpa"), 0.05),
            self._phase_press,
            0.08,
        )

        gas_noise = self._smooth_noise(
            elapsed_s,
            to_float(noise_cfg.get("gas_ohms"), 130.0),
            self._phase_gas,
            0.22,
        )

        drift_cfg = self.sensor_model.get("drift_per_min") or {}

        temp_drift = to_float(drift_cfg.get("temp_c"), 0.01)
        hum_drift = to_float(drift_cfg.get("hum_pct"), -0.015)
        press_drift = to_float(drift_cfg.get("press_hpa"), 0.002)
        gas_drift = to_float(drift_cfg.get("gas_ohms"), -8.0)

        electronics_heat = to_float(self.sensor_model.get("electronics_heat_c"), 0.25)

        pressure_reference = str(
            self.sensor_model.get("pressure_reference") or "sea_level"
        ).strip().lower()

        press_alt_effect = -0.12 * alt_m if pressure_reference == "sea_level" else 0.0

        temp = (
            base_temp
            + zone_effects["temp_c"]
            + self.temp_offset
            + minutes * (self.temp_trend + temp_drift)
            + electronics_heat
            + temp_noise
        )

        hum = (
            base_hum
            + zone_effects["hum_pct"]
            + self.hum_offset
            + minutes * (self.hum_trend + hum_drift)
            + hum_noise
        )

        press = (
            base_press
            + zone_effects["press_hpa"]
            + press_alt_effect
            + self.press_offset
            + minutes * (self.press_trend + press_drift)
            + press_noise
        )

        gas = (
            base_gas
            + zone_effects["gas_ohms"]
            + self.gas_offset
            + minutes * (self.gas_trend + gas_drift)
            + gas_noise
        )

        return {
            "temp_c": temp,
            "hum_pct": clamp(hum, 0.0, 100.0),
            "press_hpa": press,
            "gas_ohms": max(100.0, gas),
        }

    def _environment_at(self, elapsed_s: float, lat: float, lon: float, alt_m: float) -> dict:
        target = self._environment_target_at(elapsed_s, lat, lon, alt_m)

        response_lag_s = max(
            0.0,
            to_float(self.sensor_model.get("response_lag_s"), 4.0),
        )

        if self._last_env is None or self._last_elapsed_s is None or response_lag_s <= 0:
            self._last_env = dict(target)
            self._last_elapsed_s = elapsed_s

        else:
            dt = max(0.0, elapsed_s - self._last_elapsed_s)
            alpha = clamp(dt / (response_lag_s + dt), 0.0, 1.0)

            self._last_env = {
                key: self._last_env[key] + alpha * (target[key] - self._last_env[key])
                for key in target.keys()
            }

            self._last_elapsed_s = elapsed_s

        return {
            "temp_c": round(self._last_env["temp_c"], 3),
            "hum_pct": round(self._last_env["hum_pct"], 3),
            "press_hpa": round(self._last_env["press_hpa"], 3),
            "gas_ohms": round(self._last_env["gas_ohms"], 3),
        }

    def _choose_image_category(self, lat: float, lon: float) -> str:
        category = "healthy"

        for zone in self.zones:
            influence = zone_influence(zone, lat, lon)
            if influence <= 0:
                continue

            ztype = str(zone.get("type") or "").strip().lower()

            if ztype in ("anomaly_zone", "gas_patch", "vegetation_stress"):
                return "unhealthy"

            if ztype in ("mixed_zone", "temperature_patch"):
                category = "mixed"

        return category

    def capture_image(self, output_path: str, elapsed_s: float, lat: float, lon: float) -> bool:
        if self.image_set != "drone":
            return False

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

    def sample(self, elapsed_s: float) -> dict:
        raw_pos = self._raw_position_at(elapsed_s)
        alt_m = self._altitude_at(raw_pos, elapsed_s)

        gps = self._gps_at(raw_pos, alt_m, elapsed_s)

        env = self._environment_at(
            elapsed_s=elapsed_s,
            lat=raw_pos["lat"],
            lon=raw_pos["lon"],
            alt_m=alt_m,
        )

        return {
            **gps,
            **env,
        }

    def build_meta_block(self) -> dict:
        return {
            "enabled": True,
            "engine": "drone_v1",
            "route_id": self.route_id,
            "profile_type": self.profile_type,
            "ideal_sensor_model": False,
            "nominal_duration_s": round(self.nominal_duration_s, 3),
            "actual_duration_s": round(self.duration_s, 3),
            "route_distance_m": round(self.route_distance_m, 2),
            "seed": self.seed,
            "motion_model": self.motion_model,
            "altitude_model": self.altitude_model,
            "gps_model": self.gps_model,
            "sensor_model": self.sensor_model,
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

    def finalize(self) -> None:
        pass
      