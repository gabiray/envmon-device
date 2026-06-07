import hashlib
import math
import random

from agent.simulation.sim_utils import (
    clamp,
    meters_to_lat_lon,
    to_float,
)


def _stable_seed(*parts) -> int:
    text = "|".join(str(p) for p in parts)
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(h[:12], 16)


class StaticSimulationEngine:
    """
    Static monitoring station simulation engine.

    Behavior:
    - fixed location with very small GPS jitter;
    - no route movement and no image capture;
    - environmental values evolve over time;
    - supports drift, smooth sensor noise, daily/mission cycle and local events;
    - compatible with logger.py through the same SimulationEngine interface.
    """

    def __init__(self, route: dict, sim_state: dict):
        self.route = route
        self.sim_state = sim_state

        self.route_id = route["id"]
        self.profile_type = route.get("profile_type") or "static"

        self.waypoints = route.get("waypoints") or []
        self.base_environment = route.get("base_environment") or {}
        self.gps_model = route.get("gps_model") or {}
        self.sensor_model = route.get("sensor_model") or {}
        self.events = route.get("events") or []

        if not self.waypoints:
            raise ValueError(f"Route {self.route_id} has no waypoint")

        self.station = self.waypoints[0]

        self.duration_s = float(route.get("duration_s") or 900.0)

        seed = (
            self.sensor_model.get("seed")
            or self.gps_model.get("seed")
            or sim_state.get("selected_at_epoch")
            or self.route_id
        )

        self.seed = _stable_seed(self.route_id, seed)
        self.rng = random.Random(self.seed)

        self.temp_offset = to_float(sim_state.get("temp_offset"), 0.0)
        self.hum_offset = to_float(sim_state.get("hum_offset"), 0.0)
        self.press_offset = to_float(sim_state.get("press_offset"), 0.0)
        self.gas_offset = to_float(sim_state.get("gas_offset"), 0.0)

        self.temp_trend = to_float(sim_state.get("temp_trend"), 0.0)
        self.hum_trend = to_float(sim_state.get("hum_trend"), 0.0)
        self.press_trend = to_float(sim_state.get("press_trend"), 0.0)
        self.gas_trend = to_float(sim_state.get("gas_trend"), 0.0)

        self._phase_gps_n = self.rng.uniform(0.0, math.tau)
        self._phase_gps_e = self.rng.uniform(0.0, math.tau)
        self._phase_alt = self.rng.uniform(0.0, math.tau)

        self._phase_temp = self.rng.uniform(0.0, math.tau)
        self._phase_hum = self.rng.uniform(0.0, math.tau)
        self._phase_press = self.rng.uniform(0.0, math.tau)
        self._phase_gas = self.rng.uniform(0.0, math.tau)

        self._phase_cycle = self.rng.uniform(0.0, math.tau)

        self._last_elapsed_s = None
        self._last_env = None

    def is_finished(self, elapsed_s: float) -> bool:
        return elapsed_s >= self.duration_s

    def _base_position(self) -> dict:
        return {
            "lat": to_float(self.station.get("lat"), 0.0),
            "lon": to_float(self.station.get("lon"), 0.0),
            "alt_m": to_float(
                self.station.get("alt_m")
                if self.station.get("alt_m") is not None
                else self.station.get("alt"),
                0.0,
            ),
        }

    def _gps_at(self, elapsed_s: float) -> dict:
        pos = self._base_position()

        lat = pos["lat"]
        lon = pos["lon"]
        alt_m = pos["alt_m"]

        horizontal_noise_m = abs(
            to_float(self.gps_model.get("horizontal_noise_m"), 0.7)
        )

        vertical_noise_m = abs(
            to_float(self.gps_model.get("vertical_noise_m"), 1.4)
        )

        # Small smooth jitter. The point remains visually static,
        # but telemetry is not mathematically identical every sample.
        north_jitter = horizontal_noise_m * (
            0.65 * math.sin(0.22 * elapsed_s + self._phase_gps_n)
            + 0.35 * math.sin(0.055 * elapsed_s + self._phase_gps_n * 1.7)
        )

        east_jitter = horizontal_noise_m * (
            0.65 * math.sin(0.19 * elapsed_s + self._phase_gps_e)
            + 0.35 * math.sin(0.047 * elapsed_s + self._phase_gps_e * 1.5)
        )

        dlat, dlon = meters_to_lat_lon(lat, north_jitter, east_jitter)

        alt_noise = vertical_noise_m * math.sin(
            0.12 * elapsed_s + self._phase_alt
        )

        satellites_min = int(to_float(self.gps_model.get("satellites_min"), 8))
        satellites_max = int(to_float(self.gps_model.get("satellites_max"), 13))

        if satellites_max < satellites_min:
            satellites_max = satellites_min

        sat_wave = 0.5 + 0.5 * math.sin(0.025 * elapsed_s + self._phase_gps_n)
        satellites = int(
            round(satellites_min + sat_wave * (satellites_max - satellites_min))
        )

        hdop_min = to_float(self.gps_model.get("hdop_min"), 0.8)
        hdop_max = to_float(self.gps_model.get("hdop_max"), 1.6)

        hdop_wave = 0.5 + 0.5 * math.sin(0.021 * elapsed_s + self._phase_gps_e)
        hdop = hdop_min + hdop_wave * (hdop_max - hdop_min)

        return {
            "lat": round(lat + dlat, 7),
            "lon": round(lon + dlon, 7),
            "alt_m": round(alt_m + alt_noise, 3),
            "fix_quality": 1,
            "satellites": satellites,
            "hdop": round(hdop, 2),
        }

    def _smooth_noise(
        self,
        elapsed_s: float,
        amplitude: float,
        phase: float,
        speed: float,
    ) -> float:
        return amplitude * (
            0.65 * math.sin(speed * elapsed_s + phase)
            + 0.35 * math.sin(speed * 0.33 * elapsed_s + phase * 1.9)
        )

    def _event_envelope(self, elapsed_s: float, start_s: float, duration_s: float) -> float:
        if duration_s <= 0:
            return 0.0

        end_s = start_s + duration_s

        if elapsed_s < start_s or elapsed_s > end_s:
            return 0.0

        local_t = elapsed_s - start_s

        # Smooth fade-in and fade-out so events do not appear as hard jumps.
        ramp_s = min(duration_s * 0.20, 45.0)

        if ramp_s <= 0:
            return 1.0

        if local_t < ramp_s:
            x = local_t / ramp_s
            return x * x * (3.0 - 2.0 * x)

        if elapsed_s > end_s - ramp_s:
            x = (end_s - elapsed_s) / ramp_s
            return x * x * (3.0 - 2.0 * x)

        return 1.0

    def _event_effects_at(self, elapsed_s: float) -> dict:
        effects = {
            "temp_c": 0.0,
            "hum_pct": 0.0,
            "press_hpa": 0.0,
            "gas_ohms": 0.0,
        }

        for event in self.events:
            start_s = to_float(event.get("start_s"), 0.0)
            duration_s = to_float(event.get("duration_s"), 0.0)
            strength = to_float(event.get("strength"), 1.0)

            envelope = self._event_envelope(elapsed_s, start_s, duration_s)
            if envelope <= 0:
                continue

            factor = envelope * strength
            explicit = event.get("effects")

            if isinstance(explicit, dict):
                effects["temp_c"] += to_float(explicit.get("temp_c"), 0.0) * factor
                effects["hum_pct"] += to_float(explicit.get("hum_pct"), 0.0) * factor
                effects["press_hpa"] += to_float(explicit.get("press_hpa"), 0.0) * factor
                effects["gas_ohms"] += to_float(explicit.get("gas_ohms"), 0.0) * factor
                continue

            event_type = str(event.get("type") or "").strip().lower()

            if event_type == "air_quality_drop":
                effects["gas_ohms"] -= 2400.0 * factor
                effects["temp_c"] += 0.15 * factor
                effects["hum_pct"] -= 0.5 * factor

            elif event_type == "humidity_change":
                effects["hum_pct"] += 2.5 * factor
                effects["temp_c"] -= 0.2 * factor
                effects["gas_ohms"] += 300.0 * factor

            elif event_type == "temperature_rise":
                effects["temp_c"] += 1.0 * factor
                effects["hum_pct"] -= 1.5 * factor

            elif event_type == "pressure_change":
                effects["press_hpa"] += 0.25 * factor

        return effects

    def _cycle_effects_at(self, elapsed_s: float) -> dict:
        cycle = self.sensor_model.get("cycle") or {}

        enabled = bool(cycle.get("enabled", True))
        if not enabled:
            return {
                "temp_c": 0.0,
                "hum_pct": 0.0,
                "press_hpa": 0.0,
                "gas_ohms": 0.0,
            }

        period_s = max(
            60.0,
            to_float(cycle.get("period_s"), max(self.duration_s, 60.0)),
        )

        angle = math.tau * (elapsed_s / period_s) + self._phase_cycle

        temp_amp = to_float(cycle.get("temp_c_amplitude"), 0.35)
        hum_amp = to_float(cycle.get("hum_pct_amplitude"), 1.2)
        press_amp = to_float(cycle.get("press_hpa_amplitude"), 0.06)
        gas_amp = to_float(cycle.get("gas_ohms_amplitude"), 220.0)

        temp_wave = math.sin(angle)

        # Humidity usually moves inversely to temperature.
        hum_wave = -math.sin(angle + 0.15)

        press_wave = math.sin(angle * 0.35 + 1.2)
        gas_wave = math.sin(angle * 1.3 + 0.7)

        return {
            "temp_c": temp_amp * temp_wave,
            "hum_pct": hum_amp * hum_wave,
            "press_hpa": press_amp * press_wave,
            "gas_ohms": gas_amp * gas_wave,
        }

    def _environment_target_at(
        self,
        elapsed_s: float,
        lat: float,
        lon: float,
        alt_m: float,
    ) -> dict:
        base_temp = to_float(self.base_environment.get("temp_c"), 23.0)
        base_hum = to_float(self.base_environment.get("hum_pct"), 55.0)
        base_press = to_float(self.base_environment.get("press_hpa"), 1013.0)
        base_gas = to_float(self.base_environment.get("gas_ohms"), 12500.0)

        minutes = elapsed_s / 60.0

        noise_cfg = self.sensor_model.get("noise") or {}
        drift_cfg = self.sensor_model.get("drift_per_min") or {}

        event_effects = self._event_effects_at(elapsed_s)
        cycle_effects = self._cycle_effects_at(elapsed_s)

        temp_noise = self._smooth_noise(
            elapsed_s,
            to_float(noise_cfg.get("temp_c"), 0.05),
            self._phase_temp,
            0.11,
        )

        hum_noise = self._smooth_noise(
            elapsed_s,
            to_float(noise_cfg.get("hum_pct"), 0.35),
            self._phase_hum,
            0.10,
        )

        press_noise = self._smooth_noise(
            elapsed_s,
            to_float(noise_cfg.get("press_hpa"), 0.04),
            self._phase_press,
            0.045,
        )

        gas_noise = self._smooth_noise(
            elapsed_s,
            to_float(noise_cfg.get("gas_ohms"), 110.0),
            self._phase_gas,
            0.13,
        )

        temp_drift = to_float(drift_cfg.get("temp_c"), 0.012)
        hum_drift = to_float(drift_cfg.get("hum_pct"), -0.018)
        press_drift = to_float(drift_cfg.get("press_hpa"), 0.001)
        gas_drift = to_float(drift_cfg.get("gas_ohms"), -5.0)

        electronics_heat = to_float(self.sensor_model.get("electronics_heat_c"), 0.2)

        pressure_reference = str(
            self.sensor_model.get("pressure_reference") or "local"
        ).strip().lower()

        press_alt_effect = -0.12 * alt_m if pressure_reference == "sea_level" else 0.0

        temp = (
            base_temp
            + self.temp_offset
            + minutes * (self.temp_trend + temp_drift)
            + electronics_heat
            + cycle_effects["temp_c"]
            + event_effects["temp_c"]
            + temp_noise
        )

        hum = (
            base_hum
            + self.hum_offset
            + minutes * (self.hum_trend + hum_drift)
            + cycle_effects["hum_pct"]
            + event_effects["hum_pct"]
            + hum_noise
        )

        press = (
            base_press
            + press_alt_effect
            + self.press_offset
            + minutes * (self.press_trend + press_drift)
            + cycle_effects["press_hpa"]
            + event_effects["press_hpa"]
            + press_noise
        )

        gas = (
            base_gas
            + self.gas_offset
            + minutes * (self.gas_trend + gas_drift)
            + cycle_effects["gas_ohms"]
            + event_effects["gas_ohms"]
            + gas_noise
        )

        return {
            "temp_c": temp,
            "hum_pct": clamp(hum, 0.0, 100.0),
            "press_hpa": press,
            "gas_ohms": max(100.0, gas),
        }

    def _environment_at(
        self,
        elapsed_s: float,
        lat: float,
        lon: float,
        alt_m: float,
    ) -> dict:
        target = self._environment_target_at(
            elapsed_s=elapsed_s,
            lat=lat,
            lon=lon,
            alt_m=alt_m,
        )

        response_lag_s = max(
            0.0,
            to_float(self.sensor_model.get("response_lag_s"), 8.0),
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

    def capture_image(
        self,
        output_path: str,
        elapsed_s: float,
        lat: float,
        lon: float,
    ) -> bool:
        return False

    def sample(self, elapsed_s: float) -> dict:
        gps = self._gps_at(elapsed_s)

        env = self._environment_at(
            elapsed_s=elapsed_s,
            lat=gps["lat"],
            lon=gps["lon"],
            alt_m=gps["alt_m"],
        )

        return {
            **gps,
            **env,
        }

    def build_meta_block(self) -> dict:
        return {
            "enabled": True,
            "engine": "static_v1",
            "route_id": self.route_id,
            "profile_type": self.profile_type,
            "ideal_sensor_model": False,
            "duration_s": round(self.duration_s, 3),
            "seed": self.seed,
            "station": self.station,
            "gps_model": self.gps_model,
            "sensor_model": self.sensor_model,
            "events": self.events,
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
      