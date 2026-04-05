import json
import time
from pathlib import Path

from agent.runtime.device_state import set_gps_status

SIM_DIR = Path(__file__).resolve().parents[1] / "storage" / "simulation"
SIM_STATE_FILE = SIM_DIR / "simulation_state.json"

DEFAULT_STATE = {
    "enabled": False,
    "armed": False,
    "scenario_id": None,
    "temp_offset": 0.0,
    "hum_offset": 0.0,
    "press_offset": 0.0,
    "gas_offset": 0.0,
    "temp_trend": 0.0,
    "hum_trend": 0.0,
    "press_trend": 0.0,
    "gas_trend": 0.0,
    "selected_at_epoch": None,
}


def _ensure_dir() -> None:
    SIM_DIR.mkdir(parents=True, exist_ok=True)


def _merge_defaults(data: dict | None) -> dict:
    out = dict(DEFAULT_STATE)
    if isinstance(data, dict):
        for key, value in data.items():
            out[key] = value
    return out


def load_simulation_state() -> dict:
    _ensure_dir()

    if not SIM_STATE_FILE.exists():
        return dict(DEFAULT_STATE)

    try:
        data = json.loads(SIM_STATE_FILE.read_text(encoding="utf-8"))
        return _merge_defaults(data)
    except Exception:
        return dict(DEFAULT_STATE)


def save_simulation_state(state: dict) -> dict:
    _ensure_dir()
    merged = _merge_defaults(state)

    tmp = SIM_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    tmp.replace(SIM_STATE_FILE)

    return merged


def arm_simulation(
    scenario_id: str,
    temp_offset: float = 0.0,
    hum_offset: float = 0.0,
    press_offset: float = 0.0,
    gas_offset: float = 0.0,
    temp_trend: float = 0.0,
    hum_trend: float = 0.0,
    press_trend: float = 0.0,
    gas_trend: float = 0.0,
) -> dict:
    state = {
        "enabled": True,
        "armed": True,
        "scenario_id": str(scenario_id).strip(),
        "temp_offset": float(temp_offset),
        "hum_offset": float(hum_offset),
        "press_offset": float(press_offset),
        "gas_offset": float(gas_offset),
        "temp_trend": float(temp_trend),
        "hum_trend": float(hum_trend),
        "press_trend": float(press_trend),
        "gas_trend": float(gas_trend),
        "selected_at_epoch": int(time.time()),
    }
    return save_simulation_state(state)


def clear_simulation() -> dict:
    return save_simulation_state(DEFAULT_STATE)


def disarm_simulation() -> dict:
    state = load_simulation_state()
    state["armed"] = False
    state["enabled"] = False
    return save_simulation_state(state)

def set_simulation_standby_gps(first_point: dict) -> dict:
    """
    Publish a simulated GPS fix in device_state while the simulator is armed
    but the mission has not started yet.
    """
    now = round(time.time(), 3)

    lat = float(first_point["lat"])
    lon = float(first_point["lon"])
    alt_m = float(first_point.get("alt_m") or 0.0)

    gps = {
        "online": True,
        "has_fix": True,
        "last_seen_epoch": now,
        "fix_quality": 1,
        "satellites": 12,
        "hdop": 0.8,
        "last_good_fix": {
            "ts_epoch": now,
            "fix_quality": 1,
            "satellites": 12,
            "hdop": 0.8,
            "lat": lat,
            "lon": lon,
            "alt_m": alt_m,
        },
    }
    return set_gps_status(gps)


def clear_simulation_standby_gps() -> dict:
    """
    Clear the standby GPS snapshot when simulation is disarmed.
    """
    gps = {
        "online": False,
        "has_fix": False,
        "last_seen_epoch": None,
        "fix_quality": 0,
        "satellites": 0,
        "hdop": 99.99,
        "last_good_fix": None,
    }
    return set_gps_status(gps)
