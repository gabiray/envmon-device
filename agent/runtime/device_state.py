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
        # GPS runtime snapshot (for UI, fast status)
        "gps": {
            "online": False,          # NMEA seen recently
            "has_fix": False,         # fix_quality>0 & lat/lon present
            "last_seen_epoch": None,  # last time we saw any GGA
            "fix_quality": 0,
            "satellites": 0,
            "hdop": 99.99,
            "last_good_fix": None,    # dict with lat/lon/alt/ts if ever had fix
        },
    }


def _merge_defaults(st: dict) -> dict:
    """
    Ensures older state files get missing fields added.
    """
    d = default_state()
    if not isinstance(st, dict):
        return d

    for k, v in d.items():
        if k not in st:
            st[k] = v

    # merge gps nested dict
    if not isinstance(st.get("gps"), dict):
        st["gps"] = d["gps"]
    else:
        for k, v in d["gps"].items():
            if k not in st["gps"]:
                st["gps"][k] = v

    return st


def read_state() -> dict:
    try:
        if not STATE_FILE.exists():
            return default_state()
        st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return _merge_defaults(st)
    except Exception:
        return default_state()


def write_state(state: dict) -> None:
    state = _merge_defaults(state)
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
    if pid is not _PID_UNCHANGED:
        st["pid"] = pid
    write_state(st)
    return st


def set_gps_status(gps: dict) -> dict:
    """
    Updates only the GPS snapshot in the state file.
    """
    st = read_state()
    st["gps"] = gps
    write_state(st)
    return st
