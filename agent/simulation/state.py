import json
import time
from pathlib import Path

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
  