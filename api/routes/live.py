from flask import Blueprint, jsonify

from agent.runtime.live_telemetry import read_live_telemetry

live_bp = Blueprint("live", __name__)


@live_bp.get("/live/telemetry")
def get_live_telemetry():
    item = read_live_telemetry()
    return jsonify({
        "ok": True,
        "item": item,
    })
    