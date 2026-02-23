import time
import serial
from agent.calibration.gps_fix import parse_gga_line


class GPSReader:
    def __init__(self, port="/dev/serial0", baud=9600):
        # small timeout -> don't block the mission loop
        self.ser = serial.Serial(port, baud, timeout=0.2)

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def read_gga(self, max_wait_s: float = 0.25) -> dict | None:
        """
        Tries to read a valid GGA sentence for up to max_wait_s seconds.
        Returns dict or None if no valid GGA sentence is received in time.
        """
        deadline = time.time() + max_wait_s

        while time.time() < deadline:
            try:
                raw = self.ser.read_until(b"\n").decode(errors="ignore").strip()
            except Exception:
                return None

            if not raw:
                continue

            parsed = parse_gga_line(raw)
            if parsed:
                return parsed

        return None
    