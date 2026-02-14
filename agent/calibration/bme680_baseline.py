import json, time, statistics
from pathlib import Path

import board
import busio
import adafruit_bme680

CAL_DIR = Path(__file__).resolve().parents[1] / "storage" / "calibration"
CAL_FILE = CAL_DIR / "bme680_baseline.json"

def _open_sensor():
    i2c = busio.I2C(board.SCL, board.SDA)
    return adafruit_bme680.Adafruit_BME680_I2C(i2c, address=0x77)

def calibrate_bme680(warmup_s: int = 60, sample_s: int = 180, interval_s: float = 1.0) -> dict:
    CAL_DIR.mkdir(parents=True, exist_ok=True)
    sensor = _open_sensor()

    t0 = time.time()
    while time.time() - t0 < warmup_s:
        _ = sensor.temperature
        _ = sensor.humidity
        _ = sensor.gas
        time.sleep(1)

    gas_vals = []
    hum_vals = []

    t1 = time.time()
    while time.time() - t1 < sample_s:
        try:
            gas_vals.append(float(sensor.gas))
            hum_vals.append(float(sensor.humidity))
        except Exception:
            pass
        time.sleep(interval_s)
    if len(gas_vals) < 10:
        raise RuntimeError("Prea putine esantioane BME680 pentru baseline.")

    baseline = {
        "created_at_epoch": int(time.time()),
        "warmup_s": warmup_s,
        "sample_s": sample_s,
        "interval_s": interval_s,
        "gas_baseline_ohms": statistics.median(gas_vals[-min(len(gas_vals), 60):]),
        "hum_baseline_pct": sum(hum_vals) / len(hum_vals) if hum_vals else None,
        "notes": "Baseline practic pentru proiect (stabilizare + median gas)."
    }

    CAL_FILE.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    return baseline

def load_bme680_baseline() -> dict | None:
    if not CAL_FILE.exists():
        return None
    return json.loads(CAL_FILE.read_text(encoding="utf-8"))	







