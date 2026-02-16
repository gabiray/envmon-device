import json
import time
from pathlib import Path

STATE_FILE = Path("/tmp/envmon_device_state.json")

# sentinel to allow callers to "leave pid unchanged" when they don't pass a pid
_PID_UNCHANGED = object()


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
    pid: int | None | object = _PID_UNCHANGED,
) -> dict:
    """Update device state.

    Important: if the caller omits `pid` (the default), the existing PID in
    the state file is preserved. Callers can still clear the PID explicitly
    by passing `pid=None`.
    """
    st = read_state()
    st["state"] = new_state
    st["since_epoch"] = int(time.time())
    st["mission_id"] = mission_id
    st["profile"] = profile
    st["warnings"] = warnings or []
    st["error"] = error
    # only update pid when caller passed an explicit value (including None)
    if pid is not _PID_UNCHANGED:
        st["pid"] = pid
    write_state(st)
    return st
