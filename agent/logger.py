import argparse
import json
import signal
import threading
import time
from pathlib import Path

from agent.calibration.bme680_baseline import load_bme680_baseline
from agent.calibration.gps_fix import wait_for_gps_fix
from agent.runtime.device_state import set_state
from agent.sensors.bme680_reader import BME680Reader
from agent.sensors.camera_capture import capture_image
from agent.sensors.gps_reader import GPSReader
from agent.storage.mission_store import (
    create_mission_folder,
    write_meta,
    append_csv_row,
    new_mission_id,
    MISSIONS_DIR,
)

_stop_event = threading.Event()
_stop_reason = "STOP"  # STOP or ABORT


def _handle_stop(signum, frame):
    global _stop_reason
    if signum in (signal.SIGINT, signal.SIGUSR1):
        _stop_reason = "ABORT"
    else:
        _stop_reason = "STOP"
    _stop_event.set()


signal.signal(signal.SIGTERM, _handle_stop)  # stop
signal.signal(signal.SIGINT, _handle_stop)   # abort (optional)
signal.signal(signal.SIGUSR1, _handle_stop)  # abort (preferred from API)


TELEMETRY_HEADER = [
    "ts_epoch",
    "lat", "lon", "alt_m",
    "fix_quality", "satellites", "hdop",
    "temp_c", "hum_pct", "press_hpa", "gas_ohms",
]

IMAGES_HEADER = [
    "ts_epoch",
    "lat", "lon", "alt_m",
    "filename",
]


def _event_path(mission_id: str) -> Path:
    return MISSIONS_DIR / mission_id / "events.jsonl"


def emit(mission_id: str, level: str, msg: str, **fields):
    ev = {"ts_epoch": round(time.time(), 3), "level": level, "msg": msg, **fields}
    try:
        _event_path(mission_id).parent.mkdir(parents=True, exist_ok=True)
        with _event_path(mission_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        pass
    print(f"[{level}] {msg}")


def run_mission(
    mission_id: str,
    duration_s: int,
    sample_hz: float,
    photo_every_s: int,
    gps_mode: str,
    camera_mode: str,
    location_mode: str,
    fixed_lat: float | None,
    fixed_lon: float | None,
    fixed_alt: float | None,
    gps_timeout_s: int,
    gps_stable_s: int,
):
    profile = {
        "duration_s": duration_s,
        "sample_hz": sample_hz,
        "photo_every_s": photo_every_s,
        "gps_mode": gps_mode,               # off | best_effort | required
        "camera_mode": camera_mode,         # on | off
        "location_mode": location_mode,     # gps | fixed | none
        "fixed_location": {"lat": fixed_lat, "lon": fixed_lon, "alt_m": fixed_alt},
        "gps_timeout_s": gps_timeout_s,
        "gps_stable_s": gps_stable_s,
    }

    warnings = []
    set_state("ARMING", mission_id=mission_id, profile=profile, warnings=warnings)

    # Pre-flight baseline (optional)
    bme_baseline = load_bme680_baseline()

    # GPS preflight depending on mode
    gps_ready = None
    if gps_mode == "required":
        emit(mission_id, "INFO", "Waiting for required GPS fix...")
        gps_ready = wait_for_gps_fix(
            timeout_s=gps_timeout_s,
            stable_seconds=gps_stable_s,
            min_sats=4,
            max_hdop=4.0,
            verbose=False,
        )
        if not gps_ready:
            set_state("ERROR", mission_id=mission_id, profile=profile, warnings=warnings, error="GPS required but no fix.")
            emit(mission_id, "ERROR", "GPS required but no stable fix. Aborting mission.")
            return 2
    elif gps_mode == "best_effort":
        emit(mission_id, "INFO", "GPS best-effort: starting without blocking.")
    else:
        emit(mission_id, "INFO", "GPS disabled for this mission.")

    # Location mode checks
    if location_mode == "fixed":
        if fixed_lat is None or fixed_lon is None:
            set_state("ERROR", mission_id=mission_id, profile=profile, warnings=warnings, error="fixed location missing lat/lon")
            emit(mission_id, "ERROR", "Location mode=fixed but fixed_lat/fixed_lon missing.")
            return 2

    # Init sensors
    bme = BME680Reader(address=0x77)

    gps = None
    if gps_mode != "off" and location_mode == "gps":
        gps = GPSReader(port="/dev/serial0", baud=9600)

    # Create mission folder
    mdir = create_mission_folder(mission_id)
    telemetry_path = mdir / "telemetry.csv"
    images_path = mdir / "images.csv"

    meta = {
        "mission_id": mission_id,
        "started_at_epoch": int(time.time()),
        "profile": profile,
        "bme_baseline": bme_baseline,
        "gps_ready": gps_ready,
        "notes": "Recorded on device. Live via SSE.",
    }
    write_meta(mdir, meta)

    set_state("RUNNING", mission_id=mission_id, profile=profile, warnings=warnings)
    emit(mission_id, "INFO", "Mission started.", mission_id=mission_id)

    dt = 1.0 / max(sample_hz, 0.1)
    next_photo = time.time()
    img_counter = 0

    t0 = time.time()
    exit_code = 0

    try:
        while (not _stop_event.is_set()) and (time.time() - t0 < duration_s):
            now = time.time()

            # GPS / Location
            lat = lon = alt_m = None
            fix_q = sats = 0
            hdop = 99.99

            if location_mode == "fixed":
                lat, lon, alt_m = fixed_lat, fixed_lon, fixed_alt
            elif location_mode == "none":
                pass
            else:
                # gps
                if gps is not None:
                    g = gps.read_gga()
                    lat = g.get("lat")
                    lon = g.get("lon")
                    alt_m = g.get("alt_m")
                    fix_q = g.get("fix_quality", 0) or 0
                    sats = g.get("satellites", 0) or 0
                    hdop = g.get("hdop", 99.99) or 99.99

            # BME
            b = bme.read()

            row = {
                "ts_epoch": round(now, 3),
                "lat": lat,
                "lon": lon,
                "alt_m": alt_m,
                "fix_quality": fix_q,
                "satellites": sats,
                "hdop": hdop,
                **b,
            }
            append_csv_row(telemetry_path, TELEMETRY_HEADER, row)

            # Photo
            if camera_mode == "on" and photo_every_s > 0 and now >= next_photo:
                img_counter += 1
                filename = f"{img_counter:06d}.jpg"
                out_path = str(mdir / "images" / filename)

                try:
                    capture_image(out_path, width=1280, height=720, timeout_ms=600)
                    append_csv_row(images_path, IMAGES_HEADER, {
                        "ts_epoch": round(now, 3),
                        "lat": lat,
                        "lon": lon,
                        "alt_m": alt_m,
                        "filename": filename,
                    })
                except Exception as e:
                    emit(mission_id, "WARN", f"capture_image failed: {e}")

                next_photo = now + photo_every_s

            time.sleep(dt)

        if _stop_event.is_set() and _stop_reason == "ABORT":
            set_state("ABORTED", mission_id=mission_id, profile=profile, warnings=warnings)
            emit(mission_id, "WARN", "Mission aborted by user.")
        elif _stop_event.is_set():
            set_state("COMPLETED", mission_id=mission_id, profile=profile, warnings=warnings)
            emit(mission_id, "INFO", "Mission stopped by user.")
        else:
            set_state("COMPLETED", mission_id=mission_id, profile=profile, warnings=warnings)
            emit(mission_id, "INFO", "Mission finished (timer).")

    finally:
        try:
            if gps is not None:
                gps.close()
        except Exception:
            pass

        meta["ended_at_epoch"] = int(time.time())
        meta["stop_reason"] = _stop_reason if _stop_event.is_set() else "TIMER"
        write_meta(mdir, meta)

        # back to IDLE
        set_state("IDLE", mission_id=None, profile=None, warnings=[])

    return exit_code


if __name__ == "__main__":
    p = argparse.ArgumentParser()

    p.add_argument("--mission-id", type=str, default=None)

    p.add_argument("--duration", type=int, default=60)
    p.add_argument("--sample-hz", type=float, default=2.0)
    p.add_argument("--photo-every", type=int, default=5)

    p.add_argument("--gps-mode", type=str, default="best_effort", choices=["off", "best_effort", "required"])
    p.add_argument("--camera-mode", type=str, default="on", choices=["on", "off"])
    p.add_argument("--location-mode", type=str, default="gps", choices=["gps", "fixed", "none"])

    p.add_argument("--fixed-lat", type=float, default=None)
    p.add_argument("--fixed-lon", type=float, default=None)
    p.add_argument("--fixed-alt", type=float, default=None)

    p.add_argument("--gps-timeout", type=int, default=180)
    p.add_argument("--gps-stable", type=int, default=5)

    args = p.parse_args()

    mid = args.mission_id or new_mission_id()

    code = run_mission(
        mission_id=mid,
        duration_s=args.duration,
        sample_hz=args.sample_hz,
        photo_every_s=args.photo_every,
        gps_mode=args.gps_mode,
        camera_mode=args.camera_mode,
        location_mode=args.location_mode,
        fixed_lat=args.fixed_lat,
        fixed_lon=args.fixed_lon,
        fixed_alt=args.fixed_alt,
        gps_timeout_s=args.gps_timeout,
        gps_stable_s=args.gps_stable,
    )

    raise SystemExit(code)
