# api/routes/info.py (envmon-device)
from flask import Blueprint, jsonify
from pathlib import Path
import json
import uuid
import socket

info_bp = Blueprint("info", __name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # envmon-device/
ID_FILE = PROJECT_ROOT / "agent" / "storage" / "device_id.json"


def _get_or_create_uuid() -> str:
    ID_FILE.parent.mkdir(parents=True, exist_ok=True)

    if ID_FILE.exists():
        try:
            data = json.loads(ID_FILE.read_text(encoding="utf-8"))
            v = str(data.get("device_uuid") or "").strip()
            if v:
                return v
        except Exception:
            pass

    new_id = str(uuid.uuid4())
    ID_FILE.write_text(json.dumps({"device_uuid": new_id}, indent=2), encoding="utf-8")
    return new_id


@info_bp.get("/info")
def info():
    return jsonify({
        "ok": True,
        "device_uuid": _get_or_create_uuid(),
        "hostname": socket.gethostname(),
    })
