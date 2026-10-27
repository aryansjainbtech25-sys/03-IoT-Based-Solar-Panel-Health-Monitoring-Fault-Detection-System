/*
  Solar Monitor - USB serial version (NO Wi-Fi)
  Board: ESP32 Dev Module | Baud: 115200
  Prints one CSV line every ~2 s. Lines starting with '#' are status messages.
  Used by BOTH logger.py (data collection) and the backend (live dashboard),
  but only one of them can use the COM port at a time.
*/

/*
  CSV Contract - Serial Output
  Columns (6, in order): lux, ldr, temp_c, voltage_v, current_ma, power_mw
  Units: lux=lux, ldr=raw ADC 0-4095, temp_c=°C, voltage_v=V, current_ma=mA, power_mw=mW
  Precision: lux=%.1f, ldr=%d, temp_c=%.2f, voltage_v=%.3f, current_ma=%.2f, power_mw=%.2f
  Rules:
    - Lines starting with '#' are status messages and must be ignored by CSV parsers.
    - NAN appears as nan/-nan when a sensor is unavailable or disconnected.
    - No timestamp in CSV; PC-side reader adds timestamp on reception.
*/

/*
  Known Limitations
    - No Wi-Fi in this firmware.
    - No Bluetooth in this firmware.
    - No cloud (ThingSpeak or other) upload from the ESP32.
    - No TinyML inference on the ESP32.
    - No timestamp in the CSV output.
    - INA219 is calibrated for a maximum of 16V / 400mA.
    - Only one PC program can read the COM port at a time.
    - Wi-Fi, Bluetooth, cloud, and TinyML are planned for later isolated stages; do not implement them here.
*/

#include <Wire.h>
#include <Adafruit_INA219.h>
#include <BH1750.h>
#include <OneWire.h>
#include <DallasTemperature.h>

#define SDA_PIN 21
#define SCL_PIN 22
#define LDR_PIN 34
#define DS_PIN  4

Adafruit_INA219 ina219(0x40);
BH1750 lightMeter(0x23);
OneWire oneWire(DS_PIN);
DallasTemperature ds(&oneWire);

bool inaOK = false, luxOK = false;

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.flush();
  Wire.begin(SDA_PIN, SCL_PIN);
  analogReadResolution(12);

  inaOK = ina219.begin();
  if (inaOK) ina219.setCalibration_16V_400mA();
  else Serial.println("# ERROR: INA219 not found");

  luxOK = lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE);
  if (luxOK) lightMeter.setMTreg(32);
  else Serial.println("# ERROR: BH1750 not found");

  ds.begin();
  if (ds.getDeviceCount() == 0) {
    Serial.println("# ERROR: DS18B20 not found");
  }
  Serial.println("# FIRMWARE_VERSION=1");  // <- Check this line exists
  Serial.println("lux,ldr,temp_c,voltage_v,current_ma,power_mw");
}
void loop() {
  static unsigned long lastVer = 0;
  if (millis() - lastVer > 30000) {
    Serial.println("# FIRMWARE_VERSION=1");
    lastVer = millis();
  }
  float lux = luxOK ? lightMeter.readLightLevel() : NAN;

  long ldrSum = 0;
  for (int k = 0; k < 10; k++) { ldrSum += analogRead(LDR_PIN); delay(2); }
  int ldr = ldrSum / 10;

  ds.requestTemperatures();
  float t = ds.getTempCByIndex(0);
  if (t == DEVICE_DISCONNECTED_C) t = NAN;

  float v, i, p;
  if (inaOK) {
    float vSum = 0, iSum = 0;
    for (int k = 0; k < 10; k++) {
      vSum += ina219.getBusVoltage_V() + ina219.getShuntVoltage_mV() / 1000.0f;
      iSum += ina219.getCurrent_mA();
      delay(5);
    }
    v = vSum / 10.0f;
    i = iSum / 10.0f;
    if (i < 0.0f && i > -0.5f) i = 0.0f;
    p = v * i;
  } else {
    v = NAN; i = NAN; p = NAN;
  }

  if (!isnan(v) && v > 8.5f) Serial.println("# WARNING: voltage above 8.5 V - disconnect and check panel!");
  // Must match CSV header in setup() - 6 fields - increment FIRMWARE_VERSION if changed
  Serial.printf("%.1f,%d,%.2f,%.3f,%.2f,%.2f\n", lux, ldr, t, v, i, p);
  delay(1800);
}
