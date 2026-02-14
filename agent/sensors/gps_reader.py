import serial
from agent.calibration.gps_fix import _nmea_to_decimal  


class GPSReader:
    def __init__(self, port="/dev/serial0", baud=9600):
        self.ser = serial.Serial(port, baud, timeout=2)

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def read_gga(self) -> dict | None:
        while True:	
            raw = self.ser.read_until(b"\n").decode(errors="ignore").strip()
            if not raw.startswith("$") or "GGA" not in raw:
                continue

            p = raw.split(",")
            if len(p) < 10 or not p[0].endswith("GGA"):
                continue

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

            return {
                "fix_quality": fix_q,
                "satellites": sats,
                "hdop": hdop,
                "lat": lat,
                "lon": lon,
                "alt_m": alt_m,
                "raw": raw,
            }
