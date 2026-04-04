import json
from pathlib import Path

ROUTES_DIR = Path(__file__).resolve().parent / "routes"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_routes() -> list[dict]:
    ROUTES_DIR.mkdir(parents=True, exist_ok=True)

    items = []
    for path in sorted(ROUTES_DIR.glob("*.json")):
        try:
            data = _load_json(path)
            items.append({
                "id": data.get("id") or path.stem,
                "label": data.get("label") or path.stem,
                "profile_type": data.get("profile_type"),
                "description": data.get("description", ""),
                "duration_s": data.get("duration_s"),
                "path": str(path),
            })
        except Exception as e:
            items.append({
                "id": path.stem,
                "label": path.stem,
                "profile_type": None,
                "description": f"Invalid route file: {e}",
                "duration_s": None,
                "path": str(path),
                "invalid": True,
            })

    return items


def get_route_path(route_id: str) -> Path:
    route_id = str(route_id or "").strip()
    if not route_id:
        raise ValueError("route_id is required")

    path = ROUTES_DIR / f"{route_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Route not found: {route_id}")

    return path


def load_route(route_id: str) -> dict:
    path = get_route_path(route_id)
    data = _load_json(path)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid route format for {route_id}")

    data.setdefault("id", route_id)
    data.setdefault("label", route_id)
    data.setdefault("description", "")
    data.setdefault("profile_type", None)
    data.setdefault("duration_s", 600)
    data.setdefault("default_sample_hz", 2.0)
    data.setdefault("default_photo_every_s", 5)
    data.setdefault("supports_gps", True)
    data.setdefault("supports_images", False)
    data.setdefault("ideal_sensor_model", True)
    data.setdefault("base_environment", {})
    data.setdefault("waypoints", [])
    data.setdefault("zones", [])
    data.setdefault("image_set", None)

    if not isinstance(data["waypoints"], list) or len(data["waypoints"]) == 0:
        raise ValueError(f"Route {route_id} must contain at least one waypoint")

    return data
  