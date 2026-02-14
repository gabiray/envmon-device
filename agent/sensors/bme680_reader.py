import board
import busio
import adafruit_bme680


class BME680Reader:
    def __init__(self, address=0x77):
        i2c = busio.I2C(board.SCL, board.SDA)
        self.sensor = adafruit_bme680.Adafruit_BME680_I2C(i2c, address=address)

    def read(self) -> dict:
        return {
            "temp_c": float(self.sensor.temperature),
            "hum_pct": float(self.sensor.humidity),
            "press_hpa": float(self.sensor.pressure),
            "gas_ohms": float(self.sensor.gas),
        }
