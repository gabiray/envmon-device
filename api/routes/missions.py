import os
import sys
import signal
import shutil
import subprocess
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

missions_bp = Blueprint("missions", __name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]   # .../device
MISSIONS_DIR = PROJECT_ROOT / "agent" / "storage" / "missions"
PID_FILE = Path("/tmp/envmon_logger.pid")


def _pid_running(pid: int) -> bool:
    """
    True if PID exists.
    """
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _pid_is_logger(pid: int) -> bool:
    """
    Extra safety: ensure the PID belongs to our logger process.
    Checks /proc/<pid>/cmdline for 'agent.logger'.
    """
    try:
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        if not cmdline_path.exists():
            return False
        cmdline = cmdline_path.read_bytes().decode(errors="ignore").replace("\x00", " ")
        return ("agent.logger" in cmdline) or ("-m agent.logger" in cmdline)
    except Exception:
        return False


def _is_running() -> tuple[bool, int | None]:
    """
    Returns (running, pid). Cleans up stale PID files automatically.
    A mission is considered running only if:
    - PID exists AND
    - PID command line matches agent.logger
    """
    if not PID_FILE.exists():
        return False, None

    try:
        pid = int(PID_FILE.read_text().strip())
    except Exception:
        PID_FILE.unlink(missing_ok=True)
        return False, None

    if _pid_running(pid) and _pid_is_logger(pid):
        return True, pid

    # Stale/incorrect PID -> cleanup
    PID_FILE.unlink(missing_ok=True)
    return False, None


@missions_bp.get("/missions")
def list_missions():
    MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    missions = sorted([p.name for p in MISSIONS_DIR.iterdir() if p.is_dir()], reverse=True)
    running, pid = _is_running()
    return jsonify({"missions": missions, "running": running, "pid": pid})


@missions_bp.post("/missions/start")
def start_mission():
    running, pid = _is_running()
    if running:
        return jsonify({"ok": False, "error": f"Mission already running (pid={pid})"}), 409

    payload = request.get_json(silent=True) or {}
    duration = int(payload.get("duration", 60))
    sample_hz = float(payload.get("sample_hz", 2.0))
    photo_every = int(payload.get("photo_every", 5))
    no_gps_wait = bool(payload.get("no_gps_wait", False))

    cmd = [
        sys.executable, "-m", "agent.logger",
        "--duration", str(duration),
        "--sample-hz", str(sample_hz),
        "--photo-every", str(photo_every),
    ]
    if no_gps_wait:
        cmd.append("--no-gps-wait")

    proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT))
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")

    return jsonify({"ok": True, "pid": proc.pid, "no_gps_wait": no_gps_wait})


@missions_bp.post("/missions/stop")
def stop_mission():
    running, pid = _is_running()
    if not running or pid is None:
        PID_FILE.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": "No running mission"}), 404

    try:
        os.kill(pid, signal.SIGTERM)
        PID_FILE.unlink(missing_ok=True)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@missions_bp.get("/missions/<mission_id>/export")
def export_mission(mission_id: str):
    mdir = MISSIONS_DIR / mission_id
    if not mdir.exists():
        return jsonify({"ok": False, "error": "Mission not found"}), 404

    out_zip = Path("/tmp") / f"{mission_id}.zip"
    if out_zip.exists():
        out_zip.unlink()

    shutil.make_archive(str(out_zip).replace(".zip", ""), "zip", str(mdir))
    return send_file(str(out_zip), as_attachment=True, download_name=f"{mission_id}.zip")
