#include <OneWire.h>
#include <DallasTemperature.h>

#define ONE_WIRE_BUS 4
#define LDR_PIN 34
#define VOLTAGE_PIN 35
#define CURRENT_PIN 32

#define VOLTAGE_DIVIDER_RATIO 2.0
#define CURRENT_SENSITIVITY 0.185
#define CURRENT_ZERO_VOLTAGE 1.65
#define LDR_MAX 4095.0

// Convert LDR ADC to lux for fault detection
float ldrToLux(int ldrRaw) {
  if (ldrRaw <= 0) return 0;
  float lux = 100.0 * powf((LDR_MAX / (float)ldrRaw), 1.2);
  if (lux > 100000) lux = 100000;
  return lux;
}

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);

unsigned long lastVersionPrint = 0;
const unsigned long VERSION_INTERVAL = 30000;
const unsigned long SERIAL_INTERVAL = 2000;
unsigned long lastSerial = 0;

void setup() {
  Serial.begin(115200);
  delay(1000);
  sensors.begin();
  // Firmware version for backend B.3 soft-check
  Serial.println("# FIRMWARE_VERSION=1");
  Serial.println("# Solar Monitor - Serial only (low power)");
}

void loop() {
  unsigned long now = millis();
  
  // Periodic version broadcast for backend
  if (now - lastVersionPrint >= VERSION_INTERVAL) {
    lastVersionPrint = now;
    Serial.println("# FIRMWARE_VERSION=1");
  }

  if (now - lastSerial >= SERIAL_INTERVAL) {
    lastSerial = now;
    
    sensors.requestTemperatures();
    float temp_c = sensors.getTempCByIndex(0);
    if (temp_c == DEVICE_DISCONNECTED_C) {
      temp_c = NAN;
    }
    
    int ldrRaw = analogRead(LDR_PIN);
    float lux = ldrToLux(ldrRaw);
    
    // Convert ADC to actual voltage using divider ratio
    int voltRaw = analogRead(VOLTAGE_PIN);
    float voltAdc = (voltRaw / 4095.0) * 3.3;
    float voltage_v = voltAdc * VOLTAGE_DIVIDER_RATIO;
    
    // Convert ACS712 voltage to current
    int currRaw = analogRead(CURRENT_PIN);
    float currAdc = (currRaw / 4095.0) * 3.3;
    float current_a = (currAdc - CURRENT_ZERO_VOLTAGE) / CURRENT_SENSITIVITY;
    float current_ma = current_a * 1000.0;
    if (current_ma < 0) current_ma = 0;
    
    // Power for performance ratio calculation
    float power_mw = voltage_v * current_ma;
    
    // Serial CSV for backend serial_loop and logger
    if (isnan(temp_c)) {
      Serial.printf("%.1f,%d,nan,%.3f,%.2f,%.2f\n", lux, ldrRaw, voltage_v, current_ma, power_mw);
    } else {
      Serial.printf("%.1f,%d,%.2f,%.3f,%.2f,%.2f\n", lux, ldrRaw, temp_c, voltage_v, current_ma, power_mw);
    }
  }
}
