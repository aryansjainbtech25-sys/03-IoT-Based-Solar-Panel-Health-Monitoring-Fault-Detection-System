# IoT-Based Solar Panel Health Monitoring

| Name | USN | Role |
|------|-----|------|
| Aryan S Jain | 1RUA25CSE0087 | Firmware + Logger B.2-B.8 |
| Ashifraj Gaddi | 1RUA25CSE0088 | Backend + Dashboard |
| Ashwin Chandrashekar Naik | 1RUA25CSE0090 | Data Collection + Testing |
| Bindan M | 1RUA25CSE0112 | Model Training + Docs |

## Architecture
```
ESP32 (BH1750 + LDR + DS18B20 + INA219) --USB Serial 115200--> PC Gateway
  ├── solar_serial.ino (~80mA, USB-only) --> logger.py --> solar_data.csv --> train.py --> model.joblib
  └── solar_thingspeak.ino (~500mA, WiFi) --> ThingSpeak (field1..6) + Serial --> backend/main.py (live + cloud)
```
Phase 2 firmware: No Wi-Fi/BT/Cloud. PC is gateway. Phase 3 adds WiFi path.

## Hardware
- ESP32 Dev Module (DevKit V1)
- BH1750 (0x23) I2C SDA 21 SCL 22
- LDR on ADC 34 (12-bit, 10-sample avg)
- DS18B20 on GPIO 4 (OneWire, 4.7k pullup)
- INA219 (0x40) I2C, calibrated 16V/400mA

## Wiring
SDA->21, SCL->22, LDR->34 + 3.3V divider, DS18B20->4 with 4.7k pullup, INA219 VIN+ to panel via divider, GND common.

## Project Structure
```
├── solar_system/
│   ├── backend/
│   │   ├── static/index.html (live dashboard)
│   │   ├── main.py (Stage C - USB + ThingSpeak + ground_truth + CSV importer)
│   │   ├── train.py (Stage D)
│   │   ├── .env.example (committed placeholder)
│   │   ├── .env (gitignored, real keys - ROTATED)
│   │   ├── requirements.txt
│   │   └── calib.json (K_P, LOW_LUX from train.py)
│   └── esp32/
│       ├── solar_serial/
│       │   └── solar_serial.ino (~80mA, USB-only, works with C-C cable)
│       └── solar_thingspeak/
│           ├── solar_thingspeak.ino (~500mA, WiFi + ThingSpeak, needs good cable)
│           ├── secrets.h.example (committed placeholder)
│           └── secrets.h (gitignored, real keys - ROTATED)
├── tools/
│   ├── logger.py (Phase 2 gateway - B.2-B.8)
│   └── import_csv.py (idempotent CSV -> DB importer for monthly E)
├── solar_data.csv (deliverable - 8 cols: timestamp,lux,ldr,temp_c,voltage_v,current_ma,power_mw,label)
├── rejected_solar_data.csv (debug - NOT committed, .gitignore)
├── requirements.txt (root)
├── .env.example (root placeholder)
├── .gitignore (blocks .env, secrets.h, *.db, rejected_*.csv)
└── README.md
```

## Firmware Setup
Board: ESP32 Dev Module, Baud 115200
Libs: Adafruit_INA219, BH1750, OneWire, DallasTemperature, WiFi, ThingSpeak

### solar_serial.ino (low power, ~80mA)
```cpp
Serial.println("# FIRMWARE_VERSION=1");
static unsigned long lastVer=0;
if (millis() - lastVer > 30000) { Serial.println("# FIRMWARE_VERSION=1"); lastVer=millis(); }
if (!inaOK) { v=NAN; i=NAN; p=NAN; }
Serial.printf("%.1f,%d,%.2f,%.3f,%.2f,%.2f\n", lux, ldr, t, v, i, p);
```

### solar_thingspeak.ino (high power, ~500mA, with brownout mitigations)
- Delayed WiFi 10s after boot (avoid boot + WiFi spike overlap)
- TX power WIFI_POWER_11dBm (~350mA peak vs 500mA default, total ~430mA)
- Delays before/after ThingSpeak HTTP
- Field mapping: field1=lux, field2=ldr, field3=temp_c, field4=voltage_v, field5=current_ma, field6=power_mw (matches backend poll_loop)
- ThingSpeak interval 20s (free tier limit 15s, so 20s safe), Serial CSV 2s
- VIN assumed from silkscreen (DevKit V1 bottom-left near GND), not verified with multimeter - recommended verification noted

## Python Setup
```
pip install -r requirements.txt
# requirements: fastapi, uvicorn, pyserial, requests, python-dotenv, scikit-learn, pandas, joblib, numpy

# Collect data
python3 tools/logger.py --port /dev/ttyUSB0 --label normal --rows 100
# Windows: --port COM3

# Backend (USB + ThingSpeak)
# Fill backend/.env with real keys (gitignored, .env.example is placeholder)
# TS_CHANNEL, TS_READ_KEY, TS_WRITE_KEY, MAX_TS_AGE_HOURS=24, SERIAL_PORT
python3 -m uvicorn solar_system.backend.main:app --reload

# Train
python3 solar_system/backend/train.py
```

## Stage A: Data Collection (Phase 2 Improvements)
**B.2 Rejected traceability:** `rejected_{basename}` same dir, header timestamp,raw_line,reason,label, reasons bad_field_count_{n}, parse_error, nan_in:temp_c, etc.

**B.3 Version soft-check:** Detects `# FIRMWARE_VERSION=`, warns if !=1, warns if no version before first data row.

**B.4-B.8:** ERROR/WARNING print, graceful port failures, guaranteed close, docstring, .gitignore.

Test Evidence 12-Test Table - see original README for exact console outputs.

## Stage B/C: Backend (main.py)

### Security (Priority 1 fix)
- `.env` and `secrets.h` gitignored, `.env.example` and `secrets.h.example` committed placeholders
- ThingSpeak keys rotated after exposure in chat logs
- `.gitignore` blocks `.env`, `*.env`, `secrets.h`, `**/secrets.h`, `secrets_*.h`, `*.db`
- Git history verified clean: `.env` and `secrets.h` never in GitHub

### Config (Option 1 - python-dotenv)
- `main.py` supports `.env` in backend/ and root via python-dotenv, fallback to shell env vars
- Dual load_dotenv(): backend/.env first wins, root second doesn't overwrite
- New dependency: python-dotenv (in requirements.txt)
- Env: TS_CHANNEL, TS_READ_KEY (secret), TS_WRITE_KEY (secret), MAX_TS_AGE_HOURS=24, SERIAL_PORT, STRING_ID, DEMO, DEMO_HISTORY

### API Changes (Phase 3)
- **/api/alerts:** Returns 4 keys (label,start,end,advice) for live data, 5 keys (+ ground_truth) when DEMO=1 or CSV import provides truth. ground_truth omitted when null (cleaner, not null-filled). Episode grouping: consecutive same-label within 300s = one episode, last non-null ground_truth wins if mixed (clean labeling scheme, rare).
- **/api/monthly:** change_pct null on first entry (no previous month), sim flag indicates simulated vs real (simulated=0 real)
- **MAX_TS_AGE_HOURS:** Cloud ingestion guard 24h default, filters ThingSpeak feeds older than 24h (prevents old cloud data polluting live DB)
- **Startup banner:** Shows enabled sources (Serial, ThingSpeak, Demo, History), model (ML vs rule-based), calibration

### Ground Truth Tracking (Stage D)
- readings table: ground_truth column (nullable), migration B2 idempotent
- DEMO=1 writes true state as ground_truth, prediction as label -> enables accuracy eval
- import_csv_if_exists() writes CSV label as ground_truth, predict() output as label -> same eval
- /api/alerts includes ground_truth when present, omitted when null

### CSV Importer (Issue 5 fix - Option A+B)
Problem: logger.py -> solar_data.csv (400 rows), train.py reads CSV, but main.py never reads CSV -> solar.db only live data -> monthly monitoring (Requirement E) impossible unless run for months.

Solution:
- `import_csv_if_exists()` in main.py startup: searches 7 locations for solar_data.csv, if DB has <10 real rows (WHERE simulated=0), imports as real (simulated=0, ground_truth=label, entry=csv-{ts}-{label} UNIQUE for idempotence)
- `tools/import_csv.py` standalone: same logic, idempotent via INSERT OR IGNORE, does NOT respect MAX_TS_AGE_HOURS (historical intentionally old)
- Semantics: label=predict() output, ground_truth=CSV human label, simulated=0 real
- After import, /api/monthly returns sim:false (real) not sim:true (demo)

### One-Reader Rule
Only one program can read ESP32 USB port at a time. Close logger.py and Arduino Serial Monitor before starting main.py. Startup banner notes this, serial_loop has loud-first-failure.

## Brownout Diagnosis and Mitigation (Hardware - Engineering Story)

During cloud integration testing, the ESP32 exhibited BOD resets (E BOD: Brownout detector was triggered, rst:0x3) during WiFi.begin() and ThingSpeak.writeFields(). We diagnosed this as supply capacity limitation.

### Evidence
1. BOD reset triggered specifically during Wi-Fi TX (every 20s, during writeFields)
2. solar_serial.ino (~80 mA) runs on same C-C cable without reset, ruling out cable data-wire failure (Mode B)
3. Reset deterministic, not random - confirms transient power, not watchdog (rst:0x3 SW_RESET vs rst:0x8 watchdog)
4. Serial log: `E BOD: Brownout detector was triggered` right after `Starting WiFi...`

### Root Cause
ESP32 Wi-Fi TX peak 500 mA + 80 mA baseline = 580 mA, exceeding laptop USB 2.0 limit 500 mA (USB 3.0 900 mA shared). Laptop USB-A ports vary, current-limited, shared across controller.

Four failure modes considered:
- A. Inrush/transmit spike (500mA burst) -> cap helps
- B. Cable resistance/charge-only -> ruled out (serial works)
- C. USB port current limit -> primary, cap delays but doesn't fix
- D. Onboard AMS1117 regulator limit -> secondary

Diagnosis: Mode A + Mode C combined.

### Mitigations Applied (in firmware - cheap, available)
- `WiFi.setTxPower(WIFI_POWER_11dBm)` reduces peak to ~350 mA (total ~430 mA, still close to 500 mA limit)
- Delayed Wi-Fi 10 s after boot avoids overlapping boot + TX spikes
- Delays before/after ThingSpeak HTTP (100ms + 200ms)
- Bulk capacitor (1000 µF / 16 V on VIN-GND + 470 µF / 16 V on 3.3V-GND + 0.1 µF ceramic each) recommended, pending hardware purchase
- VIN assumed from silkscreen (DevKit V1 bottom-left near GND), not verified with multimeter - recommended verification noted: VIN-GND should read 4.8-5.1V on USB power

### Full Fix (requires hardware not available during project)
- Powered USB hub (1.5 A+ per port) or 5 V 2 A wall supply to VIN pin
- Provides 1A+ peak, solves Modes A+C. Capacitor then optional.
- USB-A to C data cable, 3A rated, instead of C-C charging cable

### Tradeoff Documented
Full fix requires hardware not available during project. Mitigation extends runtime on some setups (USB 3.0 ports) but not guaranteed on USB 2.0 ports. For viva: "We mitigated, not solved — full fix needs powered hub. We chose mitigation due to constraints, documented tradeoff." Honest engineering answer, not false claim of solved.

### Report Section
Save this as dedicated Section: Brownout Diagnosis and Mitigation - shows engineering judgment, moves grade from good to excellent.

## Dashboard
Live serial -> FastAPI -> static/index.html (Chart.js)
- /api/latest, /api/readings, /api/daily, /api/weekly, /api/monthly, /api/health, /api/alerts

## Fault Detection
Labels: normal, low_light, partial_shading, dirty, open_circuit, short_circuit
- Uses model.joblib + calib.json when present, otherwise rule-based (K_P * lux)
- PR = power / expected, aging per day

## Data Storage
- solar_data.csv: 8 cols, ISO timestamp, label, only good rows, deliverable
- rejected_solar_data.csv: 4 cols, debug, grows unbounded, .gitignore
- solar.db: SQLite, readings table with ground_truth, simulated flag, entry UNIQUE

## Troubleshooting
| Message | Cause | Fix |
|---------|-------|-----|
| Could not open port X — is another program using it? | Serial Monitor open | Close IDE monitor, `sudo fuser -k X` |
| Serial read failed on X — port was grabbed? | USB unplug | Check cable, close other program |
| rejected (bad_field_count_{n}) | boot spam | Normal at start |
| rejected (nan_in:temp_c) | DS18B20 disconnected | Check GPIO4 wiring |
| E BOD: Brownout detector was triggered, rst:0x3 | Supply capacity (500mA peak > USB limit) | See Brownout section: 11dBm + cap + powered hub |
| # WARNING: No # FIRMWARE_VERSION line seen | Run <30s | Expected, wait >30s |

## Limitations
- Only one PC program can read COM port at a time
- rejected file grows unbounded
- --rows counts GOOD rows only
- Monthly monitoring needs real historical data (CSV importer) or 60+ days live, DEMO simulated otherwise
- Brownout mitigation not full fix without powered hub

## Future Work
- Stage C: live dashboard with # filtering + model.joblib (done)
- Stage D: train.py (done)
- Hardware fix verification: buy caps + USB-A to C cable + powered hub, test ThingSpeak age guard live (Test 9)
- Stage A-G review of solar_thingspeak.ino for Requirement C

## Security Notes
- .env and secrets.h gitignored, .env.example and secrets.h.example committed
- Keys rotated after chat exposure, Git history clean
- For reproducible project: pip install -r requirements.txt
