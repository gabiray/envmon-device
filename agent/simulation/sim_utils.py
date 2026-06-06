import math


EARTH_RADIUS_M = 6371000.0


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def to_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_M * c


def meters_to_lat_lon(lat: float, north_m: float, east_m: float) -> tuple[float, float]:
    dlat = north_m / 111320.0

    cos_lat = math.cos(math.radians(lat))
    if abs(cos_lat) < 0.000001:
        dlon = 0.0
    else:
        dlon = east_m / (111320.0 * cos_lat)

    return dlat, dlon


def build_route_distance_table(waypoints: list[dict]) -> tuple[list[float], float]:
    cumulative = [0.0]

    if len(waypoints) <= 1:
        return cumulative, 0.0

    total = 0.0

    for idx in range(1, len(waypoints)):
        a = waypoints[idx - 1]
        b = waypoints[idx]

        d = distance_m(
            to_float(a.get("lat")),
            to_float(a.get("lon")),
            to_float(b.get("lat")),
            to_float(b.get("lon")),
        )

        total += max(d, 0.0)
        cumulative.append(total)

    return cumulative, total


def _interp_optional_number(a: dict, b: dict, key: str, t: float):
    av = a.get(key)
    bv = b.get(key)

    if av is None and bv is None:
        return None

    if av is None:
        av = bv

    if bv is None:
        bv = av

    return lerp(to_float(av), to_float(bv), t)


def interpolate_route_by_distance(
    waypoints: list[dict],
    cumulative_distances: list[float],
    target_distance_m: float,
) -> dict:
    if not waypoints:
        raise ValueError("Route must contain at least one waypoint")

    if len(waypoints) == 1:
        wp = waypoints[0]
        return {
            "lat": to_float(wp.get("lat")),
            "lon": to_float(wp.get("lon")),
            "alt_m": to_float(wp.get("alt_m") if wp.get("alt_m") is not None else wp.get("alt")),
            "ground_alt_m": wp.get("ground_alt_m"),
            "segment_idx": 0,
            "segment_t": 0.0,
        }

    total_distance = cumulative_distances[-1] if cumulative_distances else 0.0
    d = clamp(target_distance_m, 0.0, total_distance)

    seg_idx = 0

    for idx in range(1, len(cumulative_distances)):
        if d <= cumulative_distances[idx]:
            seg_idx = idx - 1
            break
    else:
        seg_idx = len(waypoints) - 2

    seg_start = cumulative_distances[seg_idx]
    seg_end = cumulative_distances[seg_idx + 1]
    seg_len = max(seg_end - seg_start, 0.000001)

    t = clamp((d - seg_start) / seg_len, 0.0, 1.0)

    a = waypoints[seg_idx]
    b = waypoints[seg_idx + 1]

    alt_a = a.get("alt_m") if a.get("alt_m") is not None else a.get("alt")
    alt_b = b.get("alt_m") if b.get("alt_m") is not None else b.get("alt")

    if alt_a is None:
        alt_a = alt_b if alt_b is not None else 0.0

    if alt_b is None:
        alt_b = alt_a

    return {
        "lat": lerp(to_float(a.get("lat")), to_float(b.get("lat")), t),
        "lon": lerp(to_float(a.get("lon")), to_float(b.get("lon")), t),
        "alt_m": lerp(to_float(alt_a), to_float(alt_b), t),
        "ground_alt_m": _interp_optional_number(a, b, "ground_alt_m", t),
        "segment_idx": seg_idx,
        "segment_t": t,
    }


def zone_influence(zone: dict, lat: float, lon: float) -> float:
    zlat = zone.get("lat")
    zlon = zone.get("lon")
    radius_m = to_float(zone.get("radius_m"), 0.0)
    strength = to_float(zone.get("strength"), 0.0)

    if zlat is None or zlon is None or radius_m <= 0:
        return 0.0

    d = distance_m(lat, lon, to_float(zlat), to_float(zlon))

    if d >= radius_m:
        return 0.0

    x = 1.0 - d / radius_m

    # Smooth falloff, so the effect does not jump suddenly.
    smooth = x * x * (3.0 - 2.0 * x)

    return strength * smooth
  