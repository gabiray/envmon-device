from pathlib import Path
import os

from flask import Blueprint, jsonify

from agent.runtime.device_state import read_state, set_state

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


@status_bp.get("/status")
def status():
    st = read_state()

    running = False
    pid = None

    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            # zombie => not running
            running = _pid_running_non_zombie(pid)
            if not running:
                PID_FILE.unlink(missing_ok=True)
        except Exception:
            PID_FILE.unlink(missing_ok=True)
            running = False
            pid = None

    # Fix stale state if needed
    if st.get("state") in ("ARMING", "RUNNING") and not running:
        set_state("IDLE", mission_id=None, profile=None, warnings=["Stale RUNNING state corrected."], error=None, pid=None)
        st = read_state()

    st["running"] = running
    st["pid"] = pid if running else None
    return jsonify(st)
