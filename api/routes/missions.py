import os
import sys
import signal
import shutil
import subprocess
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from agent.runtime.device_state import set_state, read_state
from agent.storage.mission_store import new_mission_id

missions_bp = Blueprint("missions", __name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]   # .../envmon-device
MISSIONS_DIR = PROJECT_ROOT / "agent" / "storage" / "missions"
PID_FILE = Path("/tmp/envmon_logger.pid")


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _pid_is_logger(pid: int) -> bool:
    try:
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        if not cmdline_path.exists():
            return False
        cmdline = cmdline_path.read_bytes().decode(errors="ignore").replace("\x00", " ")
        return ("agent.logger" in cmdline) or ("-m agent.logger" in cmdline)
    except Exception:
        return False


def _is_running() -> tuple[bool, int | None]:
    if not PID_FILE.exists():
        return False, None
    try:
        pid = int(PID_FILE.read_text().strip())
    except Exception:
        PID_FILE.unlink(missing_ok=True)
        return False, None

    if _pid_running(pid) and _pid_is_logger(pid):
        return True, pid

    PID_FILE.unlink(missing_ok=True)
    return False, None


@missions_bp.get("/missions")
def list_missions():
    MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    missions = sorted([p.name for p in MISSIONS_DIR.iterdir() if p.is_dir()], reverse=True)
    running, pid = _is_running()

    st = read_state()
    st["running"] = running
    st["pid"] = pid

    return jsonify({"missions": missions, **st})


@missions_bp.post("/missions/start")
def start_mission():
    running, pid = _is_running()
    if running:
        return jsonify({"ok": False, "error": f"Mission already running (pid={pid})"}), 409

    payload = request.get_json(silent=True) or {}

    duration = int(payload.get("duration", 60))
    sample_hz = float(payload.get("sample_hz", 2.0))
    photo_every = int(payload.get("photo_every", 5))

    gps_mode = str(payload.get("gps_mode", "best_effort"))          # off|best_effort|required
    camera_mode = str(payload.get("camera_mode", "on"))            # on|off
    location_mode = str(payload.get("location_mode", "gps"))       # gps|fixed|none

    fixed = payload.get("fixed_location") or {}
    fixed_lat = fixed.get("lat", None)
    fixed_lon = fixed.get("lon", None)
    fixed_alt = fixed.get("alt_m", None)

    gps_timeout = int(payload.get("gps_timeout_s", 180))
    gps_stable = int(payload.get("gps_stable_s", 5))

    mission_id = new_mission_id()

    cmd = [
        sys.executable, "-m", "agent.logger",
        "--mission-id", mission_id,
        "--duration", str(duration),
        "--sample-hz", str(sample_hz),
        "--photo-every", str(photo_every),
        "--gps-mode", gps_mode,
        "--camera-mode", camera_mode,
        "--location-mode", location_mode,
        "--gps-timeout", str(gps_timeout),
        "--gps-stable", str(gps_stable),
    ]

    if fixed_lat is not None:
        cmd += ["--fixed-lat", str(fixed_lat)]
    if fixed_lon is not None:
        cmd += ["--fixed-lon", str(fixed_lon)]
    if fixed_alt is not None:
        cmd += ["--fixed-alt", str(fixed_alt)]

    proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT))
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")

    profile = {
        "duration_s": duration,
        "sample_hz": sample_hz,
        "photo_every_s": photo_every,
        "gps_mode": gps_mode,
        "camera_mode": camera_mode,
        "location_mode": location_mode,
        "fixed_location": {"lat": fixed_lat, "lon": fixed_lon, "alt_m": fixed_alt},
        "gps_timeout_s": gps_timeout,
        "gps_stable_s": gps_stable,
    }

    set_state("ARMING", mission_id=mission_id, profile=profile, warnings=[], pid=proc.pid)

    return jsonify({"ok": True, "pid": proc.pid, "mission_id": mission_id, "profile": profile})


@missions_bp.post("/missions/stop")
def stop_mission():
    running, pid = _is_running()
    if not running or pid is None:
        PID_FILE.unlink(missing_ok=True)
        set_state("IDLE", mission_id=None, profile=None, warnings=[], pid=None)
        return jsonify({"ok": False, "error": "No running mission"}), 404

    try:
        os.kill(pid, signal.SIGTERM)
        PID_FILE.unlink(missing_ok=True)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@missions_bp.post("/missions/abort")
def abort_mission():
    running, pid = _is_running()
    if not running or pid is None:
        PID_FILE.unlink(missing_ok=True)
        set_state("IDLE", mission_id=None, profile=None, warnings=[], pid=None)
        return jsonify({"ok": False, "error": "No running mission"}), 404

    try:
        # Prefer SIGUSR1 -> logger marks ABORT
        os.kill(pid, signal.SIGUSR1)
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
