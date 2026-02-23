from flask import Blueprint, jsonify
import subprocess

from agent.sensors.bme680_reader import BME680Reader
from agent.calibration.gps_fix import wait_for_gps_fix

health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health():
    result = {"ok": True, "checks": {}, "warnings": []}

    # BME680 (REQUIRED)
    try:
        bme = BME680Reader(address=0x77)
        sample = bme.read()
        result["checks"]["bme680"] = {"ok": True, "sample": sample}
    except Exception as e:
        result["ok"] = False
        result["checks"]["bme680"] = {"ok": False, "error": str(e)}
        return jsonify(result)  

    # GPS (optional -> warning)
    try:
        gps = wait_for_gps_fix(timeout_s=4, stable_seconds=1, min_sats=4, max_hdop=10.0, verbose=False)
        ok = bool(gps)
        result["checks"]["gps"] = {"ok": ok, "details": gps}
        if not ok:
            result["warnings"].append("GPS not fixed (optional depending on mission profile).")
    except Exception as e:
        result["checks"]["gps"] = {"ok": False, "error": str(e)}
        result["warnings"].append("GPS check failed (optional depending on mission profile).")

    # Camera (optional -> warning)
    try:
        res = subprocess.run(["which", "rpicam-still"], capture_output=True, text=True)
        ok = (res.returncode == 0)
        result["checks"]["camera"] = {"ok": ok}
        if not ok:
            result["warnings"].append("Camera not available (optional depending on mission profile).")
    except Exception as e:
        result["checks"]["camera"] = {"ok": False, "error": str(e)}
        result["warnings"].append("Camera check failed (optional depending on mission profile).")

    return jsonify(result)
