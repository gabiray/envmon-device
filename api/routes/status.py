from pathlib import Path
import os
import time

from flask import Blueprint, jsonify, request

from agent.runtime.device_state import read_state, set_state, set_gps_status
from agent.sensors.gps_reader import GPSReader

from agent.simulation.state import load_simulation_state

status_bp = Blueprint("status", __name__)
PID_FILE = Path("/tmp/envmon_logger.pid")


def _proc_state(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="ignore")
        after = stat.split(") ", 1)[1]
        return after.split(" ", 1)[0]
    except Exception:
        return None


def _pid_running_non_zombie(pid: int) -> bool:
    if _proc_state(pid) == "Z":
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _read_live_gps_snapshot(max_wait_s: float = 1.5) -> dict:
    t0 = time.time()
    gr = None
    try:
        gr = GPSReader(port="/dev/serial0", baud=9600)
        g = gr.read_gga(max_wait_s=max_wait_s)
    finally:
        if gr is not None:
            try:
                gr.close()
            except Exception:
                pass

    now = round(time.time(), 3)

    if not g:
        return {
            "online": False,
            "has_fix": False,
            "last_seen_epoch": None,
            "fix_quality": 0,
            "satellites": 0,
            "hdop": 99.99,
            "last_good_fix": None,
            "elapsed_s": round(time.time() - t0, 3),
        }

    fix_q = int(g.get("fix_quality") or 0)
    sats = int(g.get("satellites") or 0)
    hdop = float(g.get("hdop") or 99.99)
    lat = g.get("lat")
    lon = g.get("lon")
    alt_m = g.get("alt_m")
    has_fix = bool(fix_q > 0 and lat is not None and lon is not None)

    return {
        "online": True,
        "has_fix": has_fix,
        "last_seen_epoch": now,
        "fix_quality": fix_q,
        "satellites": sats,
        "hdop": hdop,
        "last_good_fix": {
            "ts_epoch": now,
            "fix_quality": fix_q,
            "satellites": sats,
            "hdop": hdop,
            "lat": lat,
            "lon": lon,
            "alt_m": alt_m,
        } if has_fix else None,
        "elapsed_s": round(time.time() - t0, 3),
    }


@status_bp.get("/status")
def status():
    st = read_state()

    running = False
    pid = None

    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            running = _pid_running_non_zombie(pid)
            if not running:
                PID_FILE.unlink(missing_ok=True)
        except Exception:
            PID_FILE.unlink(missing_ok=True)
            running = False
            pid = None

    if st.get("state") in ("ARMING", "RUNNING") and not running:
        set_state(
            "IDLE",
            mission_id=None,
            profile=None,
            warnings=["Stale RUNNING state corrected."],
            error=None,
            pid=None,
        )
        st = read_state()

    # IMPORTANT:
    # - if mission is running, trust GPS snapshot maintained by logger
    # - if device is idle and simulation is armed, keep simulated standby GPS
    # - otherwise, refresh real GPS live here
    if not running:
        sim_state = load_simulation_state()
        sim_armed = bool(sim_state.get("enabled") and sim_state.get("armed"))

        # SIMULATION:
        # When a scenario is armed but mission has not started yet,
        # keep the standby GPS snapshot already published from the first waypoint.
        if not sim_armed:
            try:
                max_wait_s = float(request.args.get("gps_wait_s") or 1.5)
            except Exception:
                max_wait_s = 1.5

            try:
                live_gps = _read_live_gps_snapshot(max_wait_s=max_wait_s)

                prev_last_good = (st.get("gps") or {}).get("last_good_fix")
                if live_gps.get("last_good_fix") is None and prev_last_good:
                    live_gps["last_good_fix"] = prev_last_good

                set_gps_status({
                    "online": live_gps["online"],
                    "has_fix": live_gps["has_fix"],
                    "last_seen_epoch": live_gps["last_seen_epoch"],
                    "fix_quality": live_gps["fix_quality"],
                    "satellites": live_gps["satellites"],
                    "hdop": live_gps["hdop"],
                    "last_good_fix": live_gps["last_good_fix"],
                })
                st = read_state()
            except Exception:
                pass
