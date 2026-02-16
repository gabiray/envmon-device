import os
import sys
import signal
import shutil
import subprocess
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
import time
import json

from agent.runtime.device_state import set_state, read_state
from agent.storage.mission_store import new_mission_id, create_mission_folder, write_meta

missions_bp = Blueprint("missions", __name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # .../envmon-device
MISSIONS_DIR = PROJECT_ROOT / "agent" / "storage" / "missions"
PID_FILE = Path("/tmp/envmon_logger.pid")


# ---------------------------
# Process helpers
# ---------------------------
def _proc_state(pid: int) -> str | None:
    """
    Returns Linux process state letter from /proc/<pid>/stat:
      R,S,D,T,t,Z,X,...
    Z == zombie.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="ignore")
        # format: pid (comm) state ...
        after = stat.split(") ", 1)[1]
        return after.split(" ", 1)[0]
    except Exception:
        return None


def _pid_running_non_zombie(pid: int) -> bool:
    """
    True if PID exists AND is not a zombie.
    """
    st = _proc_state(pid)
    if st == "Z":
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _pid_is_logger(pid: int) -> bool:
    """
    Safety: ensure PID belongs to our logger process.
    Note: for zombie processes cmdline may be empty -> treat as not logger.
    """
    try:
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        if not cmdline_path.exists():
            return False
        cmdline = cmdline_path.read_bytes().decode(errors="ignore").replace("\x00", " ")
        return ("agent.logger" in cmdline) or ("-m agent.logger" in cmdline)
    except Exception:
        return False


def _cleanup_pidfile_if_matches(pid: int):
    try:
        if PID_FILE.exists() and PID_FILE.read_text().strip() == str(pid):
            PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _try_reap(pid: int):
    """
    Reap child process to avoid zombies.
    Works only if PID is our child.
    """
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        # not our child (e.g. after restart) -> nothing to reap
        pass
    except Exception:
        pass


def _is_running() -> tuple[bool, int | None]:
    """
    Returns (running, pid). Cleans up stale PID files.
    Consider mission running ONLY if:
      - PID exists AND
      - not zombie AND
      - cmdline matches agent.logger
    """
    if not PID_FILE.exists():
        return False, None

    try:
        pid = int(PID_FILE.read_text().strip())
    except Exception:
        PID_FILE.unlink(missing_ok=True)
        return False, None

    # If zombie: reap + cleanup
    if _proc_state(pid) == "Z":
        _try_reap(pid)
        PID_FILE.unlink(missing_ok=True)
        return False, None

    if _pid_running_non_zombie(pid) and _pid_is_logger(pid):
        return True, pid

    # stale/incorrect PID -> cleanup
    PID_FILE.unlink(missing_ok=True)
    return False, None


def _kill(pid: int, sig: int) -> None:
    """
    Prefer killing the process group (helps stop camera subprocess too),
    fallback to killing pid.
    """
    try:
        os.killpg(pid, sig)
    except Exception:
        os.kill(pid, sig)


def _watch_and_reap(proc: subprocess.Popen, mission_id: str):
    """
    Background reaper thread:
    - waits for the child to finish -> prevents zombies
    - cleans PID file (if still matches)
    - if state is stuck RUNNING/ARMING with same pid, force IDLE
    """
    pid = proc.pid
    try:
        proc.wait()
    except Exception:
        pass

    _cleanup_pidfile_if_matches(pid)

    st = read_state()
    # If logger did not reset the state (crash / forced kill), fix it
    if st.get("pid") == pid and st.get("mission_id") == mission_id and st.get("state") in ("ARMING", "RUNNING"):
        set_state(
            "IDLE",
            mission_id=None,
            profile=None,
            warnings=["Mission process ended (reaper cleanup)."],
            error=None,
            pid=None,
        )


# ---------------------------
# Routes
# ---------------------------
@missions_bp.get("/missions")
def list_missions():
    MISSIONS_DIR.mkdir(parents=True, exist_ok=True)

    valid = []
    incomplete = []
    missions_meta: dict[str, dict] = {}

    for p in sorted(MISSIONS_DIR.iterdir(), key=lambda x: x.name, reverse=True):
        if not p.is_dir():
            continue
        meta_path = p / "meta.json"
        if not meta_path.exists():
            incomplete.append(p.name)
            continue
        try:
            missions_meta[p.name] = json.loads(meta_path.read_text(encoding="utf-8"))
            valid.append(p.name)
        except Exception:
            # unreadable/corrupt meta -> treat as incomplete
            incomplete.append(p.name)

    missions = valid

    running, pid = _is_running()
    st = read_state()
    # Merge runtime truth into state response
    st["running"] = running
    st["pid"] = pid

    # If state claims RUNNING but process is not running -> fix stale state
    if st.get("state") in ("ARMING", "RUNNING") and not running:
        set_state("IDLE", mission_id=None, profile=None, warnings=["Stale state corrected."], error=None, pid=None)
        st = read_state()
        st["running"] = False
        st["pid"] = None

    # keep backward-compatible `missions` (list of ids) + expose incomplete/meta
    resp = {"missions": missions, "incomplete_missions": incomplete, **st}
    if missions_meta:
        resp["missions_meta"] = missions_meta
    return jsonify(resp)


@missions_bp.post("/missions/start")
def start_mission():
    running, pid = _is_running()
    if running:
        return jsonify({"ok": False, "error": f"Mission already running (pid={pid})"}), 409

    payload = request.get_json(silent=True) or {}

    duration = int(payload.get("duration", 60))
    sample_hz = float(payload.get("sample_hz", 2.0))
    photo_every = int(payload.get("photo_every", 5))

    gps_mode = str(payload.get("gps_mode", "best_effort"))       # off|best_effort|required
    camera_mode = str(payload.get("camera_mode", "on"))         # on|off
    location_mode = str(payload.get("location_mode", "gps"))    # gps|fixed|none

    fixed = payload.get("fixed_location") or {}
    fixed_lat = fixed.get("lat", None)
    fixed_lon = fixed.get("lon", None)
    fixed_alt = fixed.get("alt_m", None)

    gps_timeout = int(payload.get("gps_timeout_s", 180))
    gps_stable = int(payload.get("gps_stable_s", 5))

    mission_id = new_mission_id()

    # create mission dir + initial meta so mission is visible immediately
    mdir = create_mission_folder(mission_id)
    write_meta(mdir, {
        "mission_id": mission_id,
        "created_at_epoch": int(time.time()),
        "profile": {
            "duration_s": duration,
            "sample_hz": sample_hz,
            "photo_every_s": photo_every,
            "gps_mode": gps_mode,
            "camera_mode": camera_mode,
            "location_mode": location_mode,
            "fixed_location": {"lat": fixed_lat, "lon": fixed_lon, "alt_m": fixed_alt},
            "gps_timeout_s": gps_timeout,
            "gps_stable_s": gps_stable,
        },
        "notes": "Initialized by API; logger will update on start.",
    })

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

    # Redirect output to a mission log file (keeps API console clean)
    log_path = (MISSIONS_DIR / mission_id / "process.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = log_path.open("a", encoding="utf-8")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # creates its own process group
        )
    except Exception:
        log_f.close()
        raise

    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    # close parent's copy of the FD (child inherited it)
    log_f.close()

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

    set_state("ARMING", mission_id=mission_id, profile=profile, warnings=[], error=None, pid=proc.pid)

    # Start background reaper to avoid zombies
    threading.Thread(target=_watch_and_reap, args=(proc, mission_id), daemon=True).start()

    return jsonify({"ok": True, "pid": proc.pid, "mission_id": mission_id, "profile": profile})


@missions_bp.post("/missions/stop")
def stop_mission():
    running, pid = _is_running()
    if not running or pid is None:
        PID_FILE.unlink(missing_ok=True)
        set_state("IDLE", mission_id=None, profile=None, warnings=[], error=None, pid=None)
        return jsonify({"ok": False, "error": "No running mission"}), 404

    try:
        _kill(pid, signal.SIGTERM)
        _cleanup_pidfile_if_matches(pid)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@missions_bp.post("/missions/abort")
def abort_mission():
    running, pid = _is_running()
    if not running or pid is None:
        PID_FILE.unlink(missing_ok=True)
        set_state("IDLE", mission_id=None, profile=None, warnings=[], error=None, pid=None)
        return jsonify({"ok": False, "error": "No running mission"}), 404

    try:
        _kill(pid, signal.SIGUSR1)  # logger marks ABORT
        _cleanup_pidfile_if_matches(pid)
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
