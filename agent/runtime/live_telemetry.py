import json
from pathlib import Path

LIVE_TELEMETRY_FILE = Path("/tmp/envmon_live_telemetry.json")


def write_live_telemetry(payload: dict) -> None:
    tmp = LIVE_TELEMETRY_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(LIVE_TELEMETRY_FILE)


def read_live_telemetry() -> dict | None:
    try:
        if not LIVE_TELEMETRY_FILE.exists():
            return None
        return json.loads(LIVE_TELEMETRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_live_telemetry() -> None:
    try:
        LIVE_TELEMETRY_FILE.unlink(missing_ok=True)
    except Exception:
        pass
      