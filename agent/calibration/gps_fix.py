import time
import serial


def _nmea_to_decimal(deg_min: str, hemisphere: str) -> float | None:
    if not deg_min or not hemisphere:
        return None

    try:
        v = float(deg_min)
    except ValueError:
        return None

    deg_len = 2 if hemisphere in ("N", "S") else 3
    deg = int(v // 100) if deg_len == 2 else int(v // 100)
    minutes = v - deg * 100
    dec = deg + minutes / 60.0

    if hemisphere in ("S", "W"):
        dec = -dec
    return dec


def wait_for_gps_fix(
    port: str = "/dev/serial0",
    baud: int = 9600,
    min_sats: int = 4,
    max_hdop: float = 4.0,
    stable_seconds: int = 5,
    timeout_s: int = 180,
    verbose: bool = True,
):

    def parse_gga(line: str):
        # $GPGGA / $GNGGA
        p = line.split(",")
        if len(p) < 10:
            return None
        if not p[0].endswith("GGA"):
            return None

        try:
            fix_q = int(p[6] or "0")
        except ValueError:
            fix_q = 0

        try:
            sats = int(p[7] or "0")
        except ValueError:
            sats = 0

        try:
            hdop = float(p[8] or "99.99")
        except ValueError:
            hdop = 99.99

        lat = _nmea_to_decimal(p[2], p[3])
        lon = _nmea_to_decimal(p[4], p[5])

        try:
            alt_m = float(p[9]) if p[9] else None
        except ValueError:
            alt_m = None

        return fix_q, sats, hdop, lat, lon, alt_m

    start = time.time()
    ok_since = None
    last_good = None

    with serial.Serial(port, baud, timeout=2) as ser:
        while time.time() - start < timeout_s:
            raw = ser.read_until(b"\n").decode(errors="ignore").strip()

            if not raw.startswith("$"):
                continue
            if "GGA" not in raw:
                continue

            parsed = parse_gga(raw)
            if not parsed:
                continue

            fix_q, sats, hdop, lat, lon, alt_m = parsed

            if verbose:
                print(
                    f"GPS: fix_q={fix_q} sats={sats} hdop={hdop:.2f} "
                    f"lat={lat} lon={lon} alt={alt_m} | {raw}"
                )

            good = (
                fix_q > 0
                and sats >= min_sats
                and hdop <= max_hdop
                and lat is not None
                and lon is not None
            )

            if good:
                if ok_since is None:
                    ok_since = time.time()

                last_good = {
                    "fix_quality": fix_q,
                    "satellites": sats,
                    "hdop": hdop,
                    "lat": lat,
                    "lon": lon,
                    "alt_m": alt_m,
                }

                if time.time() - ok_since >= stable_seconds:
                    return last_good
            else:
                ok_since = None

    return last_good
