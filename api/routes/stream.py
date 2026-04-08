import json
import time
from pathlib import Path

from flask import Blueprint, Response, stream_with_context

from agent.runtime.device_state import read_state
from agent.runtime.live_telemetry import read_live_telemetry
from agent.storage.mission_store import MISSIONS_DIR

stream_bp = Blueprint("stream", __name__)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@stream_bp.get("/stream")
def stream():
    @stream_with_context
    def gen():
        last_mission_id = None
        events_fp = None
        events_pos = 0

        last_hb_ts = 0.0
        last_live_ts = None

        while True:
            now = time.time()
            st = read_state()

            if now - last_hb_ts >= 1.0:
                last_hb_ts = now
                yield _sse(
                    "heartbeat",
                    {
                        "ts_epoch": round(now, 3),
                        "state": st.get("state"),
                        "mission_id": st.get("mission_id"),
                        "warnings": st.get("warnings", []),
                        "error": st.get("error"),
                        "pid": st.get("pid"),
                    },
                )

            mission_id = st.get("mission_id")

            if mission_id != last_mission_id:
                last_mission_id = mission_id
                events_fp = None
                events_pos = 0
                last_live_ts = None

            live = read_live_telemetry()
            if (
                mission_id
                and isinstance(live, dict)
                and live.get("mission_id") == mission_id
            ):
                current_ts = live.get("ts_epoch")
                if current_ts is not None and current_ts != last_live_ts:
                    last_live_ts = current_ts
                    yield _sse("telemetry_live", live)

            if mission_id:
                epath = MISSIONS_DIR / mission_id / "events.jsonl"
                try:
                    if events_fp is None and epath.exists():
                        events_fp = epath.open(
                            "r",
                            encoding="utf-8",
                            errors="ignore",
                        )
                        events_fp.seek(0, 2)
                        events_pos = events_fp.tell()

                    if events_fp is not None:
                        events_fp.seek(events_pos)
                        for line in events_fp.readlines():
                            events_pos = events_fp.tell()
                            line = line.strip()
                            if not line:
                                continue
                            yield _sse(
                                "log",
                                {"mission_id": mission_id, "event": line},
                            )
                except Exception:
                    pass

            time.sleep(0.05)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return Response(gen(), mimetype="text/event-stream", headers=headers)
