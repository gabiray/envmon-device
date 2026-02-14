from flask import Blueprint, jsonify
import subprocess

from agent.sensors.bme680_reader import BME680Reader
from agent.calibration.gps_fix import wait_for_gps_fix

health_bp = Blueprint("health", __name__)

@health_bp.get("/health")
def health():
    result = {"ok": True, "checks": {}}

    # BME680
    try:
        bme = BME680Reader(address=0x77)
        sample = bme.read()
        result["checks"]["bme680"] = {"ok": True, "sample": sample}
    except Exception as e:
        result["ok"] = False
        result["checks"]["bme680"] = {"ok": False, "error": str(e)}

    try:
        gps = wait_for_gps_fix(timeout_s=8, stable_seconds=2, min_sats=4, max_hdop=10.0, verbose=False)
        result["checks"]["gps"] = {"ok": bool(gps), "details": gps}
    except Exception as e:
        result["checks"]["gps"] = {"ok": False, "error": str(e)}

    try:
        res = subprocess.run(["which", "rpicam-still"], capture_output=True, text=True)
        ok = (res.returncode == 0)
        result["checks"]["camera"] = {"ok": ok}
        if not ok:
            result["ok"] = False
    except Exception as e:
        result["ok"] = False
        result["checks"]["camera"] = {"ok": False, "error": str(e)}

    return jsonify(result)
