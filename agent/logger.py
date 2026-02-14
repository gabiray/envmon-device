import argparse
import time
from pathlib import Path
import signal
import threading

from agent.sensors.bme680_reader import BME680Reader
from agent.sensors.gps_reader import GPSReader
from agent.sensors.camera_capture import capture_image
from agent.storage.mission_store import (
    new_mission_id,
    create_mission_folder,
    write_meta,
    append_csv_row,
)
from agent.calibration.bme680_baseline import load_bme680_baseline
from agent.calibration.gps_fix import wait_for_gps_fix


_stop_event = threading.Event()

def _handle_stop(signum, frame):
    _stop_event.set()

signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT, _handle_stop)


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


def run_mission(duration_s: int, sample_hz: float, photo_every_s: int, no_gps_wait: bool = False):
    # 1) Pre-flight: baseline BME + GPS fix
    bme_baseline = load_bme680_baseline()
	
    gps_ready = None
    if no_gps_wait:
        print("DEBUG: Skipping GPS wait (start immediately).")
    else:
        gps_ready = wait_for_gps_fix(
            timeout_s=0,
		    stable_seconds=5,
		    min_sats=4,
	        max_hdop=4.0,
		    verbose=True
		)
    if not gps_ready:
        print("WARN: No stable GPS fix. Starting anyway (GPS may be weak).")

    # 2) Init readers
    bme = BME680Reader(address=0x77)
    gps = GPSReader(port="/dev/serial0", baud=9600)

    # 3) Create mission folder
    mission_id = new_mission_id()
    mdir = create_mission_folder(mission_id)
    telemetry_path = mdir / "telemetry.csv"
    images_path = mdir / "images.csv"

    meta = {
        "mission_id": mission_id,
        "started_at_epoch": int(time.time()),
        "duration_s": duration_s,
        "sample_hz": sample_hz,
        "photo_every_s": photo_every_s,
        "bme_baseline": bme_baseline,
        "gps_ready": gps_ready,
        "notes": "Mission recorded on device. Live stream optional.",
    }
    write_meta(mdir, meta)

    # 4) Loop
    dt = 1.0 / sample_hz
    next_photo = time.time()
    img_counter = 0

    t0 = time.time()
    try:
        while (not _stop_event.is_set()) and (time.time() - t0 < duration_s):
            now = time.time()

            # GPS
            g = gps.read_gga()
            # BME
            b = bme.read()

            row = {
                "ts_epoch": round(now, 3),
                "lat": g["lat"],
                "lon": g["lon"],
                "alt_m": g["alt_m"],
                "fix_quality": g["fix_quality"],
                "satellites": g["satellites"],
                "hdop": g["hdop"],
                **b,
            }
            append_csv_row(telemetry_path, TELEMETRY_HEADER, row)

            # Photo
            if photo_every_s > 0 and now >= next_photo:
                img_counter += 1
                filename = f"{img_counter:06d}.jpg"
                out_path = str(mdir / "images" / filename)

                try:
                    capture_image(out_path, width=1280, height=720, timeout_ms=600)
                    append_csv_row(images_path, IMAGES_HEADER, {
                        "ts_epoch": round(now, 3),
                        "lat": g["lat"],
                        "lon": g["lon"],
                        "alt_m": g["alt_m"],
                        "filename": filename,
                    })
                except Exception as e:
                    print("WARN: capture_image failed:", e)

                next_photo = now + photo_every_s

            time.sleep(dt)
    finally:
        gps.close()

    # 5) Finalize meta
    meta["ended_at_epoch"] = int(time.time())
    write_meta(mdir, meta)

    print(f"Mission finished: {mission_id}")
    print(f"Saved to: {mdir}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--duration", type=int, default=60)
    p.add_argument("--sample-hz", type=float, default=2.0)
    p.add_argument("--photo-every", type=int, default=5)
    p.add_argument("--no-gps-wait", action="store_true", help="Debug: start immediately without waiting for GPS fix")
    args = p.parse_args()

    run_mission(args.duration, args.sample_hz, args.photo_every)
