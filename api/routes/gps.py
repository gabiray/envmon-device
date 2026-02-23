from flask import Blueprint, jsonify, request
import time

from agent.calibration.gps_fix import wait_for_gps_fix, parse_gga_line
from agent.sensors.gps_reader import GPSReader

gps_bp = Blueprint("gps", __name__)


@gps_bp.get("/gps/status")
def gps_status():
    """
    Fast snapshot: tries briefly to read a GGA sentence and returns parsed fields.
    Does NOT block for long and does NOT require a full fix.
    """
    port = request.args.get("port") or "/dev/serial0"
    try:
        baud = int(request.args.get("baud") or 9600)
    except Exception:
        baud = 9600

    try:
        max_wait_s = float(request.args.get("max_wait_s") or 0.4)
    except Exception:
        max_wait_s = 0.4

    t0 = time.time()
    g = None
    try:
        gr = GPSReader(port=port, baud=baud)
        g = gr.read_gga(max_wait_s=max_wait_s)
        gr.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

    if not g:
        return jsonify({
            "ok": True,
            "has_data": False,
            "elapsed_s": round(time.time() - t0, 3),
            "gps": None,
        })

    fix_q = int(g.get("fix_quality") or 0)
    lat = g.get("lat")
    lon = g.get("lon")

    return jsonify({
        "ok": True,
        "has_data": True,
        "elapsed_s": round(time.time() - t0, 3),
        "gps": {
            "fix_quality": fix_q,
            "satellites": int(g.get("satellites") or 0),
            "hdop": float(g.get("hdop") or 99.99),
            "lat": lat,
            "lon": lon,
            "alt_m": g.get("alt_m"),
            "has_fix": bool(fix_q > 0 and lat is not None and lon is not None),
            "raw": g.get("raw"),
        }
    })


@gps_bp.post("/gps/warmup")
def gps_warmup():
    """
    Blocks until a stable GPS fix is achieved (or timeout).
    Intended for pre-flight "warmup" BEFORE starting a mission.
    """
    payload = request.get_json(silent=True) or {}

    port = payload.get("port") or "/dev/serial0"
    baud = int(payload.get("baud") or 9600)

    min_sats = int(payload.get("min_sats") or 4)
    max_hdop = float(payload.get("max_hdop") or 4.0)

    stable_seconds = int(payload.get("stable_seconds") or 5)
    timeout_s = int(payload.get("timeout_s") or 60)  # warmup default 60s (not 180)

    verbose = bool(payload.get("verbose") or False)

    t0 = time.time()
    fix = wait_for_gps_fix(
        port=port,
        baud=baud,
        min_sats=min_sats,
        max_hdop=max_hdop,
        stable_seconds=stable_seconds,
        timeout_s=timeout_s,
        verbose=verbose,
    )
    elapsed = round(time.time() - t0, 3)

    ok = bool(
        fix
        and (fix.get("fix_quality") or 0) > 0
        and fix.get("lat") is not None
        and fix.get("lon") is not None
    )

    return jsonify({
        "ok": True,
        "has_fix": ok,
        "elapsed_s": elapsed,
        "criteria": {
            "min_sats": min_sats,
            "max_hdop": max_hdop,
            "stable_seconds": stable_seconds,
            "timeout_s": timeout_s,
        },
        "fix": fix,  # may be None or last good partial
    })
    