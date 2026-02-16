import json
import time
from pathlib import Path

STATE_FILE = Path("/tmp/envmon_device_state.json")


def default_state() -> dict:
    return {
        "state": "IDLE",
        "since_epoch": int(time.time()),
        "mission_id": None,
        "profile": None,
        "warnings": [],
        "error": None,
        "pid": None,
    }


def read_state() -> dict:
    try:
        if not STATE_FILE.exists():
            return default_state()
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return default_state()


def write_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def set_state(
    new_state: str,
    mission_id: str | None = None,
    profile: dict | None = None,
    warnings: list | None = None,
    error: str | None = None,
    pid: int | None = None,
) -> dict:
    st = read_state()
    st["state"] = new_state
    st["since_epoch"] = int(time.time())
    st["mission_id"] = mission_id
    st["profile"] = profile
    st["warnings"] = warnings or []
    st["error"] = error
    st["pid"] = pid
    write_state(st)
    return st
