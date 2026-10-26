/*
  Solar Monitor - USB serial version (NO Wi-Fi)
  Board: ESP32 Dev Module | Baud: 115200
  Prints one CSV line every ~2 s. Lines starting with '#' are status messages.
  Used by BOTH logger.py (data collection) and the backend (live dashboard),
  but only one of them can use the COM port at a time.
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
  delay(1000);
  Wire.begin(SDA_PIN, SCL_PIN);
  analogReadResolution(12);

  inaOK = ina219.begin();
  if (inaOK) ina219.setCalibration_16V_400mA();
  else Serial.println("# ERROR: INA219 not found");

  luxOK = lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE);
  if (luxOK) lightMeter.setMTreg(32);   // low sensitivity: extends range for direct sun (default clips at ~65k lux)
  else Serial.println("# ERROR: BH1750 not found");

  ds.begin();
  Serial.println("lux,ldr,temp_c,voltage_v,current_ma,power_mw");
}

void loop() {
  float lux = luxOK ? lightMeter.readLightLevel() : NAN;

  long ldrSum = 0;
  for (int k = 0; k < 10; k++) { ldrSum += analogRead(LDR_PIN); delay(2); }
  int ldr = ldrSum / 10;

  ds.requestTemperatures();
  float t = ds.getTempCByIndex(0);
  if (t == DEVICE_DISCONNECTED_C) t = NAN;

  float vSum = 0, iSum = 0;
  if (inaOK) {
    for (int k = 0; k < 10; k++) {
      vSum += ina219.getBusVoltage_V() + ina219.getShuntVoltage_mV() / 1000.0f;
      iSum += ina219.getCurrent_mA();
      delay(5);
    }
  }
  float v = vSum / 10.0f;
  float i = iSum / 10.0f;
  if (i < 0.0f && i > -0.5f) i = 0.0f;
  float p = v * i;

  if (v > 8.5f) Serial.println("# WARNING: voltage above 8.5 V - disconnect and check panel!");
  Serial.printf("%.1f,%d,%.2f,%.3f,%.2f,%.2f\n", lux, ldr, t, v, i, p);
  delay(1800);
}
