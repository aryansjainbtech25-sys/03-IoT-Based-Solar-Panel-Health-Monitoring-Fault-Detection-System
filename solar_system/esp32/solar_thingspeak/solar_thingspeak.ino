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

// Convert LDR ADC value to lux for fault detection
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
const unsigned long THINGSPEAK_INTERVAL = 20000;
const unsigned long VERSION_INTERVAL = 30000;
const unsigned long SERIAL_INTERVAL = 2000;
unsigned long lastSerial = 0;

// Reconnect WiFi if disconnected to keep cloud path alive
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.println("# WARNING: WiFi disconnected, reconnecting...");
  WiFi.begin(ssid, password);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n# WiFi connected");
    Serial.print("# IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n# ERROR: WiFi connect failed");
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  sensors.begin();
  WiFi.mode(WIFI_STA);
  ThingSpeak.begin(client);
  
  // Firmware version for backend version soft-check (B.3)
  Serial.println("# FIRMWARE_VERSION=1");
  Serial.println("# Solar Monitor - Serial + ThingSpeak");
  
  connectWiFi();
}

void loop() {
  unsigned long now = millis();
  
  // Periodic version broadcast for backend to detect firmware mismatch
  if (now - lastVersionPrint >= VERSION_INTERVAL) {
    lastVersionPrint = now;
    Serial.println("# FIRMWARE_VERSION=1");
  }
  
  if (now - lastThingSpeak > 5000) {
    if (WiFi.status() != WL_CONNECTED) {
      connectWiFi();
    }
  }

  // Read sensors and push to both serial and cloud
  if (now - lastSerial >= SERIAL_INTERVAL) {
    lastSerial = now;
    
    // Read temperature, return NAN on disconnect for rejected logging (B.2)
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
    
    // Serial CSV for backend serial_loop and offline logger
    if (isnan(temp_c)) {
      Serial.printf("%.1f,%d,nan,%.3f,%.2f,%.2f\n", lux, ldrRaw, voltage_v, current_ma, power_mw);
    } else {
      Serial.printf("%.1f,%d,%.2f,%.3f,%.2f,%.2f\n", lux, ldrRaw, temp_c, voltage_v, current_ma, power_mw);
    }
    
    // ThingSpeak upload for cloud ingestion with age guard
    if (now - lastThingSpeak >= THINGSPEAK_INTERVAL) {
      lastThingSpeak = now;
      
      if (WiFi.status() == WL_CONNECTED) {
        // Field mapping must match backend poll_loop: lux, ldr, temp_c, voltage_v, current_ma, power_mw
        ThingSpeak.setField(1, lux);
        ThingSpeak.setField(2, (float)ldrRaw);
        ThingSpeak.setField(3, temp_c);
        ThingSpeak.setField(4, voltage_v);
        ThingSpeak.setField(5, current_ma);
        ThingSpeak.setField(6, power_mw);
        
        int response = ThingSpeak.writeFields(myChannelNumber, myWriteAPIKey);
        if (response == 200) {
          Serial.println("# ThingSpeak update successful");
        } else {
          Serial.printf("# ERROR: ThingSpeak failed code %d\n", response);
        }
      } else {
        Serial.println("# WARNING: Skip ThingSpeak, WiFi not connected");
      }
    }
  }
}
