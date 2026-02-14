import csv
import json
import time
import uuid
from pathlib import Path

MISSIONS_DIR = Path(__file__).resolve().parents[1] / "storage" / "missions"


def new_mission_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]


def create_mission_folder(mission_id: str) -> Path:
    mdir = MISSIONS_DIR / mission_id
    (mdir / "images").mkdir(parents=True, exist_ok=True)
    return mdir


def write_meta(mdir: Path, meta: dict):
    (mdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def append_csv_row(csv_path: Path, header: list[str], row: dict):
    exists = csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if not exists:
            w.writeheader()
        w.writerow(row)
