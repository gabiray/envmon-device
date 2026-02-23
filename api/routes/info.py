from flask import Blueprint, jsonify
from pathlib import Path
import json, uuid, socket

info_bp = Blueprint("info", __name__)
ID_FILE = Path(__file__).resolve().parents[2] / "agent" / "storage" / "device_id.json"


def get_or_create_uuid():
    ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if ID_FILE.exists():
        try:
            data = json.loads(ID_FILE.read_text(encoding="utf-8"))
            if data.get("device_uuid"):
                return data["device_uuid"]
        except Exception:
            pass
    u = str(uuid.uuid4())
    ID_FILE.write_text(json.dumps({"device_uuid": u}, indent=2), encoding="utf-8")
    return u


@info_bp.get("/info")
def info():
    return jsonify({"ok": True, "device_uuid": get_or_create_uuid(), "hostname": socket.gethostname()})