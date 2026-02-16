from flask import Blueprint, jsonify
from agent.runtime.device_state import read_state

status_bp = Blueprint("status", __name__)


@status_bp.get("/status")
def status():
    return jsonify(read_state())
