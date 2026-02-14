from agent.calibration.bme680_baseline import load_bme680_baseline
from agent.calibration.gps_fix import wait_for_gps_fix

print("BME baseline:", load_bme680_baseline())
print("GPS fix:", wait_for_gps_fix(timeout_s=20))
