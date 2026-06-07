from agent.simulation.route_loader import load_route
from agent.simulation.state import disarm_simulation
from agent.simulation.sim_utils import (
    build_route_distance_table,
    clamp,
    interpolate_route_by_distance,
    to_float,
    zone_influence,
)
from agent.simulation.engine_drone import DroneSimulationEngine
from agent.simulation.engine_car import CarSimulationEngine
from agent.simulation.engine_static import StaticSimulationEngine


class GenericSimulationEngine:
    """
    Fallback engine for bicycle / static until those profiles get dedicated engines.
    """

    def __init__(self, route: dict, sim_state: dict):
        self.route = route
        self.sim_state = sim_state

        self.route_id = route["id"]
        self.profile_type = route.get("profile_type")
        self.duration_s = float(route.get("duration_s") or 600)

        self.waypoints = route.get("waypoints") or []
        self.zones = route.get("zones") or []
        self.base_environment = route.get("base_environment") or {}

        if not self.waypoints:
            raise ValueError(f"Route {self.route_id} has no waypoints")

        self.cumulative_distances, self.route_distance_m = build_route_distance_table(
            self.waypoints
        )

        self.temp_offset = to_float(sim_state.get("temp_offset"), 0.0)
        self.hum_offset = to_float(sim_state.get("hum_offset"), 0.0)
        self.press_offset = to_float(sim_state.get("press_offset"), 0.0)
        self.gas_offset = to_float(sim_state.get("gas_offset"), 0.0)

        self.temp_trend = to_float(sim_state.get("temp_trend"), 0.0)
        self.hum_trend = to_float(sim_state.get("hum_trend"), 0.0)
        self.press_trend = to_float(sim_state.get("press_trend"), 0.0)
        self.gas_trend = to_float(sim_state.get("gas_trend"), 0.0)

    def is_finished(self, elapsed_s: float) -> bool:
        return elapsed_s >= self.duration_s

    def _position_at(self, elapsed_s: float) -> dict:
        progress = clamp(elapsed_s / max(self.duration_s, 1.0), 0.0, 1.0)
        target_distance = progress * self.route_distance_m

        pos = interpolate_route_by_distance(
            self.waypoints,
            self.cumulative_distances,
            target_distance,
        )

        return {
            "lat": pos["lat"],
            "lon": pos["lon"],
            "alt_m": pos["alt_m"],
        }

    def _environment_at(
        self,
        elapsed_s: float,
        lat: float,
        lon: float,
        alt_m: float,
    ) -> dict:
        base_temp = to_float(self.base_environment.get("temp_c"), 20.0)
        base_hum = to_float(self.base_environment.get("hum_pct"), 55.0)
        base_press = to_float(self.base_environment.get("press_hpa"), 1013.0)
        base_gas = to_float(self.base_environment.get("gas_ohms"), 12000.0)

        temp_local = 0.0
        hum_local = 0.0
        press_local = 0.0
        gas_local = 0.0

        for zone in self.zones:
            effect = zone_influence(zone, lat, lon)
            ztype = str(zone.get("type") or "").strip().lower()

            explicit = zone.get("effects")
            if isinstance(explicit, dict):
                temp_local += to_float(explicit.get("temp_c"), 0.0) * effect
                hum_local += to_float(explicit.get("hum_pct"), 0.0) * effect
                press_local += to_float(explicit.get("press_hpa"), 0.0) * effect
                gas_local += to_float(explicit.get("gas_ohms"), 0.0) * effect
                continue

            if ztype == "temperature_patch":
                temp_local += 2.0 * effect
            elif ztype == "humidity_patch":
                hum_local += 5.0 * effect
            elif ztype == "pressure_patch":
                press_local += 1.2 * effect
            elif ztype == "gas_patch":
                gas_local -= 1500.0 * effect
            elif ztype == "anomaly_zone":
                temp_local += 1.2 * effect
                gas_local -= 1000.0 * effect
            elif ztype == "mixed_zone":
                temp_local += 0.6 * effect
                hum_local -= 2.0 * effect
                gas_local -= 500.0 * effect

        minutes = elapsed_s / 60.0

        temp = base_temp + temp_local + self.temp_offset + minutes * self.temp_trend
        hum = base_hum + hum_local + self.hum_offset + minutes * self.hum_trend
        press = base_press + press_local + self.press_offset + minutes * self.press_trend
        gas = base_gas + gas_local + self.gas_offset + minutes * self.gas_trend

        return {
            "temp_c": round(temp, 3),
            "hum_pct": round(clamp(hum, 0.0, 100.0), 3),
            "press_hpa": round(press, 3),
            "gas_ohms": round(max(100.0, gas), 3),
        }

    def capture_image(self, output_path: str, elapsed_s: float, lat: float, lon: float) -> bool:
        return False

    def sample(self, elapsed_s: float) -> dict:
        pos = self._position_at(elapsed_s)

        env = self._environment_at(
            elapsed_s=elapsed_s,
            lat=pos["lat"],
            lon=pos["lon"],
            alt_m=pos["alt_m"],
        )

        return {
            "lat": pos["lat"],
            "lon": pos["lon"],
            "alt_m": pos["alt_m"],
            "fix_quality": 1,
            "satellites": 10,
            "hdop": 1.2,
            **env,
        }

    def build_meta_block(self) -> dict:
        return {
            "enabled": True,
            "engine": "generic_distance_v1",
            "route_id": self.route_id,
            "profile_type": self.profile_type,
            "ideal_sensor_model": True,
            "duration_s": self.duration_s,
            "route_distance_m": round(self.route_distance_m, 2),
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


class SimulationEngine:
    """
    Public simulation engine used by logger.py.

    Keeps the same interface:
    - from_state(...)
    - sample(...)
    - capture_image(...)
    - is_finished(...)
    - build_meta_block(...)
    - finalize(...)
    """

    def __init__(self, sim_state: dict):
        scenario_id = str(sim_state.get("scenario_id") or "").strip()
        if not scenario_id:
            raise ValueError("simulation scenario_id missing")

        route = load_route(scenario_id)
        profile_type = str(route.get("profile_type") or "").strip().lower()

        if profile_type == "drone":
            self.impl = DroneSimulationEngine(route, sim_state)
        elif profile_type == "car":
            self.impl = CarSimulationEngine(route, sim_state)
        elif profile_type == "static":
            self.impl = StaticSimulationEngine(route, sim_state)
        else:
            self.impl = GenericSimulationEngine(route, sim_state)

        self.route_id = self.impl.route_id
        self.profile_type = self.impl.profile_type
        self.duration_s = self.impl.duration_s

    @classmethod
    def from_state(cls, sim_state: dict) -> "SimulationEngine":
        return cls(sim_state)

    def is_finished(self, elapsed_s: float) -> bool:
        return self.impl.is_finished(elapsed_s)

    def sample(self, elapsed_s: float) -> dict:
        return self.impl.sample(elapsed_s)

    def capture_image(self, output_path: str, elapsed_s: float, lat: float, lon: float) -> bool:
        return self.impl.capture_image(output_path, elapsed_s, lat, lon)

    def build_meta_block(self) -> dict:
        return self.impl.build_meta_block()

    def finalize(self) -> None:
        try:
            self.impl.finalize()
        finally:
            disarm_simulation()
            