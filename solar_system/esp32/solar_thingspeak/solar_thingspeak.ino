/*
  Solar Monitor - WiFi + ThingSpeak version
  Hardware brownout fix (E BOD):
    - ESP32 WiFi draws 500-600mA spike during WiFi.begin() + ThingSpeak.writeFields()
    - Fix: 470uF or 1000uF electrolytic cap between 3.3V and GND (or 5V and GND)
        + 0.1uF ceramic in parallel
    - Also: Use USB-A to C data+power cable, 5V 2A charger if possible
    - Software: delayed WiFi, reduced TX power
*/

#include <WiFi.h>
#include <ThingSpeak.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include "secrets.h"

const char* ssid = SECRET_SSID;
const char* password = SECRET_PASS;
unsigned long myChannelNumber = SECRET_CH_ID;
const char* myWriteAPIKey = SECRET_WRITE_APIKEY;

#define ONE_WIRE_BUS 4
#define LDR_PIN 34
#define VOLTAGE_PIN 35
#define CURRENT_PIN 32

#define VOLTAGE_DIVIDER_RATIO 2.0
#define CURRENT_SENSITIVITY 0.185
#define CURRENT_ZERO_VOLTAGE 1.65
#define LDR_MAX 4095.0

float ldrToLux(int ldrRaw) {
  if (ldrRaw <= 0) return 0;
  float lux = 100.0 * powf((LDR_MAX / (float)ldrRaw), 1.2);
  if (lux > 100000) lux = 100000;
  return lux;
}

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);
WiFiClient client;

unsigned long lastThingSpeak = 0;
unsigned long lastVersionPrint = 0;
unsigned long wifiStartAttempt = 0;
bool wifiStarted = false;

const unsigned long THINGSPEAK_INTERVAL = 20000;
const unsigned long VERSION_INTERVAL = 30000;
const unsigned long SERIAL_INTERVAL = 2000;
const unsigned long WIFI_DELAY_MS = 10000;  // Delay WiFi 10s to avoid brownout
unsigned long lastSerial = 0;

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (!wifiStarted) {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.setTxPower(WIFI_POWER_11dBm);  // Reduced from 19.5dBm -> ~30% less peak
    // If still brownout: use WIFI_POWER_8_5dBm
    wifiStarted = true;
  }
  Serial.println("# WARNING: WiFi disconnected, reconnecting...");
  WiFi.begin(ssid, password);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500); Serial.print("."); attempts++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n# WiFi connected");
    Serial.print("# IP: "); Serial.println(WiFi.localIP());
    Serial.print("# RSSI: "); Serial.print(WiFi.RSSI()); Serial.println(" dBm");
  } else {
    Serial.println("\n# ERROR: WiFi connect failed");
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("# Solar Monitor - WiFi + ThingSpeak (needs 470uF cap on 3.3V-GND)");
  Serial.println("# FIRMWARE_VERSION=1");
  Serial.println("# Brownout mitigation: WiFi delayed 10s, TX power 11dBm");
  sensors.begin();
  analogReadResolution(12);
  ThingSpeak.begin(client);
  Serial.println("lux,ldr,temp_c,voltage_v,current_ma,power_mw");
  Serial.println("# Sensors ready, WiFi will start in 10s...");
  lastVersionPrint = millis();
  wifiStartAttempt = millis();
}

void loop() {
  unsigned long now = millis();
  if (now - lastVersionPrint >= VERSION_INTERVAL) {
    Serial.println("# FIRMWARE_VERSION=1");
    lastVersionPrint = now;
  }
  // Delayed WiFi start - critical fix
  if (!wifiStarted && (now - wifiStartAttempt >= WIFI_DELAY_MS)) {
    Serial.println("# Starting WiFi (delayed to avoid brownout)...");
    connectWiFi();
  }
  if (wifiStarted && WiFi.status() != WL_CONNECTED && (now - lastThingSpeak > 10000)) {
    connectWiFi();
  }
  if (now - lastSerial >= SERIAL_INTERVAL) {
    lastSerial = now;
    sensors.requestTemperatures();
    float temp_c = sensors.getTempCByIndex(0);
    if (temp_c == DEVICE_DISCONNECTED_C) temp_c = NAN;
    int ldrRaw = analogRead(LDR_PIN);
    float lux = ldrToLux(ldrRaw);
    int voltRaw = analogRead(VOLTAGE_PIN);
    float voltAdc = (voltRaw / 4095.0) * 3.3;
    float voltage_v = voltAdc * VOLTAGE_DIVIDER_RATIO;
    int currRaw = analogRead(CURRENT_PIN);
    float currAdc = (currRaw / 4095.0) * 3.3;
    float current_a = (currAdc - CURRENT_ZERO_VOLTAGE) / CURRENT_SENSITIVITY;
    float current_ma = current_a * 1000.0;
    if (current_ma < 0) current_ma = 0;
    float power_mw = voltage_v * current_ma;

    if (isnan(temp_c)) {
      Serial.printf("%.1f,%d,nan,%.3f,%.2f,%.2f\n", lux, ldrRaw, voltage_v, current_ma, power_mw);
    } else {
      Serial.printf("%.1f,%d,%.2f,%.3f,%.2f,%.2f\n", lux, ldrRaw, temp_c, voltage_v, current_ma, power_mw);
    }

    if (wifiStarted && WiFi.status() == WL_CONNECTED && (now - lastThingSpeak >= THINGSPEAK_INTERVAL)) {
      lastThingSpeak = now;
      ThingSpeak.setField(1, lux);
      ThingSpeak.setField(2, (float)ldrRaw);
      ThingSpeak.setField(3, temp_c);
      ThingSpeak.setField(4, voltage_v);
      ThingSpeak.setField(5, current_ma);
      ThingSpeak.setField(6, power_mw);
      delay(100);  // Let power stabilize before HTTP
      int response = ThingSpeak.writeFields(myChannelNumber, myWriteAPIKey);
      if (response == 200) Serial.println("# ThingSpeak update successful");
      else Serial.printf("# ERROR: ThingSpeak failed code %d\n", response);
      delay(200);
    }
  }

  delay(30000);
}
