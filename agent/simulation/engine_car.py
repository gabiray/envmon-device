import hashlib
import math
import random

from agent.simulation.sim_utils import (
    build_route_distance_table,
    clamp,
    interpolate_route_by_distance,
    meters_to_lat_lon,
    to_float,
    zone_influence,
)


def _stable_seed(*parts) -> int:
    text = "|".join(str(p) for p in parts)
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(h[:12], 16)


class CarSimulationEngine:
    """
    Realistic-ish car simulation engine.

    Main behavior:
    - distance-based route interpolation;
    - city speed profile with smooth variations and short stops;
    - no unrealistic waypoint-index speed spikes;
    - GPS jitter slightly worse than drone;
    - environmental values have noise, drift, lag and traffic/pollution zones;
    - images are disabled for car profile.
    """

    def __init__(self, route: dict, sim_state: dict):
        self.route = route
        self.sim_state = sim_state

        self.route_id = route["id"]
        self.profile_type = route.get("profile_type") or "car"

        self.waypoints = route.get("waypoints") or []
        self.zones = route.get("zones") or []
        self.base_environment = route.get("base_environment") or {}

        if not self.waypoints:
            raise ValueError(f"Route {self.route_id} has no waypoints")

        self.speed_model = route.get("speed_model") or {}
        self.gps_model = route.get("gps_model") or {}
        self.sensor_model = route.get("sensor_model") or {}

        self.cumulative_distances, self.route_distance_m = build_route_distance_table(
            self.waypoints
        )

        self.nominal_duration_s = float(route.get("duration_s") or 420.0)

        seed = (
            self.speed_model.get("seed")
            or sim_state.get("selected_at_epoch")
            or self.route_id
        )

        self.seed = _stable_seed(self.route_id, seed)
        self.rng = random.Random(self.seed)

        duration_variation_pct = abs(
            to_float(self.speed_model.get("duration_variation_pct"), 0.06)
        )

        duration_factor = 1.0 + self.rng.uniform(
            -duration_variation_pct,
            duration_variation_pct,
        )

        self.duration_s = max(30.0, self.nominal_duration_s * duration_factor)

        max_speed_kmh = to_float(self.speed_model.get("max_speed_kmh"), 60.0)
        max_speed_mps = max_speed_kmh / 3.6

        min_possible_duration = (
            self.route_distance_m / max(max_speed_mps * 0.88, 0.1)
            if self.route_distance_m > 0
            else self.duration_s
        )

        # Prevent impossible speeds if the route is too long for the configured duration.
        if self.duration_s < min_possible_duration:
            self.duration_s = min_possible_duration

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
        self._phase_temp = self.rng.uniform(0.0, math.tau)
        self._phase_hum = self.rng.uniform(0.0, math.tau)
        self._phase_press = self.rng.uniform(0.0, math.tau)
        self._phase_gas = self.rng.uniform(0.0, math.tau)

        self.stop_events = self._build_stop_events()

        self._last_elapsed_s = None
        self._last_env = None

    def _build_stop_events(self) -> list[dict]:
        stop_probability = clamp(
            to_float(self.speed_model.get("stop_probability"), 0.08),
            0.0,
            0.6,
        )

        stop_min = to_float(
            self.speed_model.get("stop_duration_s_min"),
            to_float(self.speed_model.get("stop_duration_s"), 5.0),
        )

        stop_max = to_float(
            self.speed_model.get("stop_duration_s_max"),
            max(stop_min, to_float(self.speed_model.get("stop_duration_s"), 10.0)),
        )

        if stop_max < stop_min:
            stop_max = stop_min

        events = []

        # Interpret stop_probability as chance per minute of urban driving.
        minute_count = int(max(1, self.duration_s // 60))

        for minute_idx in range(1, minute_count):
            if self.rng.random() > stop_probability:
                continue

            start = minute_idx * 60.0 + self.rng.uniform(-10.0, 10.0)
            start = clamp(start, 10.0, max(10.0, self.duration_s - 20.0))
            duration = self.rng.uniform(stop_min, stop_max)

            events.append({
                "start_s": start,
                "end_s": min(start + duration, self.duration_s - 1.0),
            })

        # Avoid too many stops in a short demo.
        return sorted(events[:4], key=lambda item: item["start_s"])

    def is_finished(self, elapsed_s: float) -> bool:
        return elapsed_s >= self.duration_s

    def _stopped_time_until(self, elapsed_s: float) -> float:
        total = 0.0

        for ev in self.stop_events:
            start = ev["start_s"]
            end = ev["end_s"]

            if elapsed_s <= start:
                continue

            total += max(0.0, min(elapsed_s, end) - start)

        return total

    def _total_stop_time(self) -> float:
        return sum(max(0.0, ev["end_s"] - ev["start_s"]) for ev in self.stop_events)

    def _integrated_wave(
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
        elapsed_s = clamp(elapsed_s, 0.0, self.duration_s)

        total_stop = self._total_stop_time()
        stopped_so_far = self._stopped_time_until(elapsed_s)

        moving_duration = max(1.0, self.duration_s - total_stop)
        moving_elapsed = max(0.0, elapsed_s - stopped_so_far)

        p = clamp(moving_elapsed / moving_duration, 0.0, 1.0)

        speed_variation_pct = clamp(
            abs(to_float(self.speed_model.get("speed_variation_pct"), 0.16)),
            0.0,
            0.35,
        )

        # Smooth city-driving variation.
        value = self._integrated_wave(
            p,
            speed_variation_pct,
            3.0,
            self._phase_speed_1,
        )

        value = self._integrated_wave(
            value,
            speed_variation_pct * 0.45,
            7.0,
            self._phase_speed_2,
        )

        end = self._integrated_wave(
            1.0,
            speed_variation_pct,
            3.0,
            self._phase_speed_1,
        )

        end = self._integrated_wave(
            end,
            speed_variation_pct * 0.45,
            7.0,
            self._phase_speed_2,
        )

        if abs(end) < 0.000001:
            return p

        return clamp(value / end, 0.0, 1.0)

    def _position_at(self, elapsed_s: float) -> dict:
        progress = self._motion_progress(elapsed_s)
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
            "segment_idx": pos.get("segment_idx", 0),
            "segment_t": pos.get("segment_t", 0.0),
        }

    def _gps_at(self, pos: dict, elapsed_s: float) -> dict:
        lat = pos["lat"]
        lon = pos["lon"]
        alt_m = pos["alt_m"]

        horizontal_noise_m = abs(
            to_float(self.gps_model.get("horizontal_noise_m"), 2.5)
        )

        vertical_noise_m = abs(
            to_float(self.gps_model.get("vertical_noise_m"), 3.0)
        )

        # Car GPS is usually a bit noisier because of buildings, roads, trees etc.
        north_jitter = horizontal_noise_m * (
            0.65 * math.sin(0.62 * elapsed_s + self._phase_gps_n)
            + 0.35 * math.sin(0.17 * elapsed_s + self._phase_gps_n * 0.8)
        )

        east_jitter = horizontal_noise_m * (
            0.65 * math.sin(0.58 * elapsed_s + self._phase_gps_e)
            + 0.35 * math.sin(0.21 * elapsed_s + self._phase_gps_e * 0.7)
        )

        dlat, dlon = meters_to_lat_lon(lat, north_jitter, east_jitter)

        alt_noise = vertical_noise_m * math.sin(
            0.37 * elapsed_s + self._phase_gps_n * 1.3
        )

        satellites_min = int(to_float(self.gps_model.get("satellites_min"), 6))
        satellites_max = int(to_float(self.gps_model.get("satellites_max"), 12))

        if satellites_max < satellites_min:
            satellites_max = satellites_min

        sat_wave = 0.5 + 0.5 * math.sin(0.04 * elapsed_s + self._phase_gps_n)
        satellites = int(
            round(satellites_min + sat_wave * (satellites_max - satellites_min))
        )

        hdop_min = to_float(self.gps_model.get("hdop_min"), 1.0)
        hdop_max = to_float(self.gps_model.get("hdop_max"), 2.6)

        hdop_wave = 0.5 + 0.5 * math.sin(0.035 * elapsed_s + self._phase_gps_e)
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

            if ztype in ("traffic_pollution", "gas_patch"):
                effects["gas_ohms"] -= 3000.0 * influence
                effects["temp_c"] += 0.35 * influence
                effects["hum_pct"] -= 1.0 * influence

            elif ztype in ("congestion_zone", "mixed_zone"):
                effects["gas_ohms"] -= 1300.0 * influence
                effects["temp_c"] += 0.5 * influence
                effects["hum_pct"] -= 1.2 * influence
                effects["press_hpa"] += 0.02 * influence

            elif ztype == "temperature_patch":
                effects["temp_c"] += 1.2 * influence
                effects["hum_pct"] -= 1.5 * influence
                effects["gas_ohms"] -= 400.0 * influence

        return effects

    def _smooth_noise(self, elapsed_s: float, amplitude: float, phase: float, speed: float) -> float:
        return amplitude * (
            0.65 * math.sin(speed * elapsed_s + phase)
            + 0.35 * math.sin(speed * 0.41 * elapsed_s + phase * 1.7)
        )

    def _environment_target_at(
        self,
        elapsed_s: float,
        lat: float,
        lon: float,
        alt_m: float,
    ) -> dict:
        base_temp = to_float(self.base_environment.get("temp_c"), 22.0)
        base_hum = to_float(self.base_environment.get("hum_pct"), 55.0)
        base_press = to_float(self.base_environment.get("press_hpa"), 1013.0)
        base_gas = to_float(self.base_environment.get("gas_ohms"), 12000.0)

        zone_effects = self._zone_effects_at(lat, lon)
        minutes = elapsed_s / 60.0

        noise_cfg = self.sensor_model.get("noise") or {}
        drift_cfg = self.sensor_model.get("drift_per_min") or {}

        temp_noise = self._smooth_noise(
            elapsed_s,
            to_float(noise_cfg.get("temp_c"), 0.12),
            self._phase_temp,
            0.16,
        )

        hum_noise = self._smooth_noise(
            elapsed_s,
            to_float(noise_cfg.get("hum_pct"), 0.6),
            self._phase_hum,
            0.14,
        )

        press_noise = self._smooth_noise(
            elapsed_s,
            to_float(noise_cfg.get("press_hpa"), 0.08),
            self._phase_press,
            0.07,
        )

        gas_noise = self._smooth_noise(
            elapsed_s,
            to_float(noise_cfg.get("gas_ohms"), 180.0),
            self._phase_gas,
            0.2,
        )

        temp_drift = to_float(drift_cfg.get("temp_c"), 0.015)
        hum_drift = to_float(drift_cfg.get("hum_pct"), -0.02)
        press_drift = to_float(drift_cfg.get("press_hpa"), 0.002)
        gas_drift = to_float(drift_cfg.get("gas_ohms"), -16.0)

        electronics_heat = to_float(self.sensor_model.get("electronics_heat_c"), 0.3)

        pressure_reference = str(
            self.sensor_model.get("pressure_reference") or "local"
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

    def _environment_at(
        self,
        elapsed_s: float,
        lat: float,
        lon: float,
        alt_m: float,
    ) -> dict:
        target = self._environment_target_at(elapsed_s, lat, lon, alt_m)

        response_lag_s = max(
            0.0,
            to_float(self.sensor_model.get("response_lag_s"), 6.0),
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

    def capture_image(self, output_path: str, elapsed_s: float, lat: float, lon: float) -> bool:
        return False

    def sample(self, elapsed_s: float) -> dict:
        pos = self._position_at(elapsed_s)
        gps = self._gps_at(pos, elapsed_s)

        env = self._environment_at(
            elapsed_s=elapsed_s,
            lat=pos["lat"],
            lon=pos["lon"],
            alt_m=pos["alt_m"],
        )

        return {
            **gps,
            **env,
        }

    def build_meta_block(self) -> dict:
        return {
            "enabled": True,
            "engine": "car_v1",
            "route_id": self.route_id,
            "profile_type": self.profile_type,
            "ideal_sensor_model": False,
            "nominal_duration_s": round(self.nominal_duration_s, 3),
            "actual_duration_s": round(self.duration_s, 3),
            "route_distance_m": round(self.route_distance_m, 2),
            "seed": self.seed,
            "speed_model": self.speed_model,
            "gps_model": self.gps_model,
            "sensor_model": self.sensor_model,
            "stop_events": self.stop_events,
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
      