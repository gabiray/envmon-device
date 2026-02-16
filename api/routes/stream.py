import json
import time
from pathlib import Path

from flask import Blueprint, Response, stream_with_context

from agent.runtime.device_state import read_state
from agent.storage.mission_store import MISSIONS_DIR

stream_bp = Blueprint("stream", __name__)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _tail_last_line(path: Path) -> str | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        size = min(end, 4096)
        f.seek(end - size)
        chunk = f.read(size)
    parts = chunk.splitlines()
    if not parts:
        return None
    return parts[-1].decode(errors="ignore")


@stream_bp.get("/stream")
def stream():
    @stream_with_context
    def gen():
        last_mission_id = None
        events_fp = None
        events_pos = 0

        last_tel_ts = 0.0
        last_hb_ts = 0.0

        while True:
            now = time.time()
            st = read_state()

            # Heartbeat each 1s
            if now - last_hb_ts >= 1.0:
                last_hb_ts = now
                yield _sse("heartbeat", {
                    "ts_epoch": round(now, 3),
                    "state": st.get("state"),
                    "mission_id": st.get("mission_id"),
                    "warnings": st.get("warnings", []),
                    "error": st.get("error"),
                    "pid": st.get("pid"),
                })

            mission_id = st.get("mission_id")

            # If mission changed, reset log tail
            if mission_id != last_mission_id:
                last_mission_id = mission_id
                events_fp = None
                events_pos = 0

            # Telemetry every 2s (only when RUNNING/ARMING etc.)
            if mission_id and now - last_tel_ts >= 2.0:
                last_tel_ts = now
                tpath = MISSIONS_DIR / mission_id / "telemetry.csv"
                last_line = _tail_last_line(tpath)
                if last_line and not last_line.startswith("ts_epoch"):
                    yield _sse("telemetry_last", {
                        "mission_id": mission_id,
                        "csv_last_line": last_line,
                    })

            # Logs tail (events.jsonl)
            if mission_id:
                epath = MISSIONS_DIR / mission_id / "events.jsonl"
                try:
                    if events_fp is None:
                        if epath.exists():
                            events_fp = epath.open("r", encoding="utf-8", errors="ignore")
                            events_fp.seek(0, 2)
                            events_pos = events_fp.tell()

                    if events_fp is not None:
                        events_fp.seek(events_pos)
                        for line in events_fp.readlines():
                            events_pos = events_fp.tell()
                            line = line.strip()
                            if not line:
                                continue
                            # each line is a JSON dict
                            yield _sse("log", {"mission_id": mission_id, "event": line})
                except Exception:
                    pass

            time.sleep(0.2)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return Response(gen(), mimetype="text/event-stream", headers=headers)
