# IoT-Based Solar Panel Health Monitoring and Fault Detection System

| Student Name | USN |
|---|---|
| Aryan S Jain | 1RUA25CSE0087 |
| Ashifraj Gaddi | 1RUA25CSE0088 |
| Ashwin Chandrashekar Naik | 1RUA25CSE0090 |
| Bindan M | 1RUA25CSE0112 |

A prototype that measures a small solar panel's electrical output and its environment with an ESP32, logs the data on a laptop, trains a machine-learning model to recognise panel conditions and faults, and shows everything on a live web dashboard with alerts and long-term performance analysis.

This version sends data **over USB serial** (no Wi-Fi). All analysis runs on the laptop.

---

## 1. What the system does

- Measures **voltage, current and power** of the panel (INA219), **light** (BH1750 and an LDR) and **temperature** (DS18B20).
- Classifies the panel state into: `normal`, `low_light`, `partial_shading`, `dirty`, `open_circuit`.
- Shows live values, the detected state, a suggested action and an alert history on a **web dashboard**.
- Calculates **daily, weekly and monthly** energy and a **performance ratio** (actual power divided by the power expected for the current light and temperature). A downward trend of that ratio indicates efficiency loss / aging.
- Plays a beep and changes the browser tab title when a new fault appears.

---

## 2. Architecture

```
 Sensors ──► ESP32 (solar_serial.ino) ──USB──► Laptop
                                               │
                       ┌───────────────────────┼─────────────────────────┐
                       ▼                       ▼                         ▼
                 tools/logger.py        backend/train.py          backend/main.py
                 (collect labelled      (calibrate + train        (read live serial, predict,
                  data to CSV)           model.joblib)             store in SQLite, serve API)
                                                                          │
                                                                          ▼
                                                              backend/static/index.html
                                                              (dashboard in the browser)
```

- The ESP32 firmware is **the same for every stage**. It only measures and prints one CSV line about every 2 seconds. You upload it once.
- **Only one program can use the COM port at a time**: Arduino Serial Monitor, `logger.py` and `main.py` each need it, so close one before starting another.
- The ML model runs on the laptop, not on the ESP32.

---

## 3. Hardware

| Part | Notes |
|---|---|
| NodeMCU ESP-32S V1.1 (ESP32) | Powered and read over USB |
| Solar panel | Small panel, listed about 6 V, 0.5 W, 100 to 120 mA. Treat the listing as a rough guess; the INA219 measures the real values |
| INA219 module | Voltage/current sensor, I2C address 0x40, 0.1 Ω shunt |
| BH1750 | Light sensor, I2C address 0x23 |
| LDR module | Analog light sensor (AO pin) |
| DS18B20 | Temperature sensor with a 4.7 kΩ pull-up |
| 3 to 6 V gear motor | Used as the panel's load |
| Breadboard, jumper wires, 4.7 kΩ resistor | |
| Laptop with Arduino IDE and Python | Windows / Linux (Linux uses /dev/ttyUSB0) |

**Do not use the 33 Ω resistor as a load.** At the panel's current it can exceed its 0.25 W rating in sun. Keep it out of the circuit.

### Wiring (unplug USB before changing any wire)

| Part | Pin | ESP32 |
|---|---|---|
| INA219 | VCC | 3V3 |
| INA219 | GND | GND |
| INA219 | SDA | GPIO 21 |
| INA219 | SCL | GPIO 22 |
| BH1750 | VCC / GND | 3V3 / GND |
| BH1750 | SDA / SCL | GPIO 21 / GPIO 22 (shared with INA219) |
| LDR module | VCC / GND | 3V3 / GND |
| LDR module | AO | GPIO 34 |
| DS18B20 | VCC / GND | 3V3 / GND |
| DS18B20 | DATA | GPIO 4, with 4.7 kΩ between DATA and 3V3 |

**Solar and load path:**

```
Panel (+) ──► INA219 VIN+ ──(internal shunt)──► INA219 VIN- ──► Motor wire 1
Motor wire 2 ──► GND rail
Panel (-)    ──► GND rail   (same rail as ESP32 GND and INA219 GND)
```

Why each connection matters:
- **VIN+ → VIN- through the module** puts the INA219's shunt in series with the load, so it can measure current.
- **Panel − on the common GND rail** is essential. The INA219 measures voltage relative to its own GND; without a shared ground the readings are meaningless.
- **The motor never connects to an ESP32 pin or 3V3.** It is powered only by the panel.
- Mount the **BH1750 next to the panel, facing the same way**, so the light reading matches what the panel receives.
- Keep the **same load for the whole dataset**. Changing the load changes every voltage and current value and invalidates the model.

### Safety checks
- Open-circuit voltage should be roughly 5 to 7 V in good light. The firmware prints `# WARNING` above 8.5 V; if you see it, unplug the panel.
- Disconnect before changing wires.
- If the ESP32 resets or the readings jump when the motor runs, motor noise is likely; unplug and rethink the wiring before continuing.
- Brownout resets (`E BOD: Brownout detector was triggered`) mean weak USB power. Use a short, good cable in a direct laptop port.

---

## 4. Project structure

Put the project outside OneDrive (for example `C:\solar_project`), because OneDrive can lock the database file. On Linux, use `~/Documents/...`.

```
solar_project  README.md
  RUNBOOK.md                 shorter checklist (this README supersedes it)
  setup.bat  collect.bat  train.bat  run_dashboard.bat  run_demo_fake.bat   optional shortcuts
  .gitignore                 ignores rejected_solar_data.csv debug artifacts
  esp32    solar_serial      solar_serial.ino       ESP32 firmware (folder name must match file name) — prints # FIRMWARE_VERSION=1 at boot + every 30s
  tools    logger.py                saves labelled sensor rows to CSV (Phase 2: with rejected logging, version check, zip-safe)
  backend    main.py                  API, serial reader, fault detection, analytics, SQLite
    train.py                 calibration + model training
    requirements.txt         Python packages
    static      index.html             dashboard
    solar_data.csv           created by logger.py (deliverable)
    solar.db                 created by main.py
    model.joblib             created by train.py
    calib.json               created by train.py
    confusion_matrix.png     created by train.py
  rejected_solar_data.csv    created by logger.py (debug artifact, gitignored) — contains boot spam, nan reasons
```

---

## 5. One-time setup

### 5.1 Arduino IDE (firmware)
1. Install the board package **esp32 by Espressif**, and these libraries: **Adafruit INA219**, **BH1750** (by Christopher Laws), **OneWire**, **DallasTemperature**.
2. Open `esp32\solar_serial\solar_serial.ino`.
3. Tools → Board → **ESP32 Dev Module**, choose your port (for example COM3 on Windows, /dev/ttyUSB0 on Linux).
4. Upload. Firmware code must contain:
   ```cpp
   // Global
   unsigned long lastVersionPrint = 0;

   void setup() {
     Serial.begin(115200);
     delay(1000);
     Serial.println("# FIRMWARE_VERSION=1"); // FIRST line before header
     lastVersionPrint = millis();
     Serial.println("lux,ldr,temp_c,voltage_v,current_ma,power_mw");
   }
   void loop() {
     // ... sensor reads ...
     if (millis() - lastVersionPrint >= 30000UL) { // non-blocking, rolling
       Serial.println("# FIRMWARE_VERSION=1");
       lastVersionPrint = millis();
     }
     // ... CSV print ...
     delay(2000);
   }
   ```
   Note: Uses `millis()` non-blocking, NOT `delay(30000)`, so CSV stays ~2s cadence.
5. Open Serial Monitor at **115200 baud**. You should see:
   ```
   # FIRMWARE_VERSION=1
   lux,ldr,temp_c,voltage_v,current_ma,power_mw
   285.8,1865,33.38,0.040,1.12,0.04
   ...
   (every 30s) # FIRMWARE_VERSION=1
   ```
   Lines starting with `#` are status or error messages (for example `# ERROR: INA219 not found`, `# WARNING: voltage above 8.5 V`).
6. **Close Serial Monitor.** (Logger and dashboard cannot use port while monitor is open)

### 5.2 Python
1. Install Python 3 from python.org and tick **Add Python to PATH**. Check with `python --version` or `python3 --version` on Linux.
2. Open a terminal in the project folder and install packages:
   ```
   python -m pip install -r backend\requirements.txt
   pip install pyserial
   ```
   (`setup.bat` runs the same command.)

---

## 6. Running each stage

Commands below are for **PowerShell** (VS Code terminal) on Windows and **bash** on Linux. Open the terminal in the project root. If your port is not COM3, replace it everywhere (Linux: /dev/ttyUSB0).

### Stage A: Collect labelled data (`tools\logger.py`) - Phase 2 Updated

```
# Windows:
python tools\logger.py --port COM3 --label normal --rows 100

# Linux:
python3 tools/logger.py --port /dev/ttyUSB0 --label normal --rows 100
```

**Phase 2 improvements (Tasks 1-10):**

- **Rejected row logging (B.2):** Creates `rejected_solar_data.csv` (same folder as `--file`) with header `timestamp,raw_line,reason,label`. Reasons:
  - `bad_field_count_{n}`: boot spam like `00`, `mw`, `try 0x4008059c`, or concatenated lines (e.g. 49,84 fields)
  - `parse_error`: float() failed
  - `nan_in:temp_c` or `nan_in:lux,temp_c` or `nan_in:voltage_v,current_ma,power_mw`: NaN fields listed by name using `zip(HEADER[1:-1], vals)` safe pairing (Issue 1 fix, avoids `HEADER[idx+1]` fragility)
  - Purpose: If you get 0 or 63/100 rows, open rejected file to see why. Clean CSV stays clean.
- **Firmware version soft-check (B.3):** Firmware sends `# FIRMWARE_VERSION=1` at boot + every 30s (millis non-blocking). Logger prints `Detected # FIRMWARE_VERSION=1` and final `Firmware: 1`. If no version seen before first data row, warns at row 1:
  ```
  # WARNING: No # FIRMWARE_VERSION line seen before first data row - firmware may have changed column order!
  # WARNING: Verify firmware sends '# FIRMWARE_VERSION=1' and CSV header matches: lux,ldr,temp_c,voltage_v,current_ma,power_mw
  ```
  Warning prints once (flag `version_warning_printed`). If run <30s, final line shows `Firmware: UNKNOWN` then `Detected` appears after 30s re-print. Long runs show `Firmware: 1`.
- **ERROR/WARNING print (B.4):** `# ERROR: DS18B20 not found` and `# WARNING: voltage above 8.5 V...` are printed to console immediately (previously silent).
- **Graceful port handling (B.5):** If port busy or non-existent (/dev/ttyUSB99), prints friendly message `Could not open port X — is another program using it?` + tip `sudo fuser -k X`, no Python traceback. Also handles read failure when another program grabs port mid-run.
- **Guaranteed close (B.6):** `try/finally` ensures `Port X closed.` always runs (normal, Ctrl+C, USB unplug). Next run won't be busy.
- **Docstring + .gitignore (B.7/B.8):** Docstring documents all above + COM3 vs /dev/ttyUSB0 note. `.gitignore` ignores `rejected_solar_data.csv` / `rejected_*.csv` (debug artifact), but NOT `solar_data.csv` (deliverable).

**Label collection:**
- One run = one condition. Change `--label` for each condition.
- Labels: `normal`, `low_light`, `partial_shading`, `dirty`, `open_circuit`.
- 100 rows takes about 3.5 minutes. `--rows 200` is better.
- Rows are appended to `solar_data.csv` with a timestamp and your label. Ctrl+C stops early and keeps what was saved. Now prints `Interrupted by user` + `Port closed.` cleanly.
- The order of runs does not matter to the code. Set up the condition first, keep it steady during the run, and leave a gap of at least 10 to 20 seconds between runs.

How to create each condition:

| Label | How |
|---|---|
| `normal` | Clean panel facing the light, load connected |
| `low_light` | Shade, or tilt the panel away from the light |
| `partial_shading` | A card covering about half the panel, held steady |
| `dirty` | Thin translucent paper or dusty film over the whole panel |
| `open_circuit` | Unplug the VIN- to motor wire so no current flows |

A good order, since the first four share the same sunlight:
1. `normal`  2. `partial_shading`  3. `dirty`  4. `open_circuit`  5. `low_light`
6. `normal` again later or at another angle  7. `low_light` again at another time

Do `normal` and `low_light` **twice** so the model can be tested on a session it did not train on. Start with a fresh `solar_data.csv` (delete or rename any old test file) and remove any wrongly labelled rows by timestamp before training. Check `rejected_solar_data.csv` if row counts don't match.

**Test Evidence (Phase 2 verification after zip fix):**
```
Test 1: Logging 5 rows as 'normal' to solar_data.csv
        1/5  35.9,1358,25.31,0.001,0.00,0.00
        # WARNING: No # FIRMWARE_VERSION line seen... (at row 1, expected for <30s run)
        5/5  35.9,1355,25.31,0.002,0.00,0.00
        Port /dev/ttyUSB0 closed.
        Done. 5 rows saved. Firmware: UNKNOWN (short run) / Firmware: 1 (long run >30s)
        +5 rows, 0 rejected — PASS, zip(HEADER[1:-1], vals) safe

Test 2: # ERROR: DS18B20 not found
        rejected (nan_in:temp_c): 21.6,1349,nan,0.002,0.00,0.00
        First rejected row: "21.6,1349,nan,0.002,0.00,0.00","nan_in:temp_c",normal
        Done. 0 rows saved. Firmware: 1 — PASS

Test 3: # ERROR: INA219 not found
        rejected (nan_in:voltage_v,current_ma,power_mw): 25.2,1313,25.31,nan,nan,nan
        First rejected row: "25.2,1310,25.38,nan,nan,nan","nan_in:voltage_v,current_ma,power_mw",normal
        PASS — proves Issue 1 fix, 3 names in HEADER order

Test 7: Ctrl+C -> Interrupted by user (Ctrl+C) + Port /dev/ttyUSB0 closed. + Done. + immediate re-run works — PASS
Test 9: Detected # FIRMWARE_VERSION=1 after 30s, final Firmware: 1 — PASS, confirms millis() non-blocking
Test 11: rm rejected, run, wc -l 1 header only, second run 9 lines, grep timestamp 1 — PASS header not duplicated
```

### Stage B: Train the model (`backend\train.py`)

```
cd backend
python train.py
```

What it does:
1. Learns **K_P** (milliwatts of power per lux for a healthy panel) from your `normal` rows, and **LOW_LUX** from the boundary between `low_light` and `normal`.
2. Computes the performance ratio feature for every row.
3. Splits data by **recording session** (a new session starts when the label changes or there is a gap above 60 seconds). The last session of each class is held out for testing. If a class has only one session, the last 30% in time is used, with a warning. This avoids the fake 99% accuracy that a random split gives on near-identical consecutive rows.
4. Trains **Decision Tree**, **Random Forest** and **Gradient Boosting**, and prints accuracy and macro-F1 for each.
5. Picks the best by macro-F1, prints a classification report and feature importances, and refits it on all data.

Outputs in `backend\`: `model.joblib`, `calib.json`, `confusion_matrix.png`. Use the confusion matrix and the printed table in your report. After training, restart the dashboard.

### Stage C: Live monitoring and alerts (`backend\main.py`)

```
cd backend
$env:SERIAL_PORT="COM3"   # Windows PowerShell
# export SERIAL_PORT=/dev/ttyUSB0  # Linux bash
$env:DEMO_HISTORY="1"
python -m uvicorn main:app --port 8000
```

Then open **http://127.0.0.1:8000** in a browser. The terminal should print `Serial connected: COM3`. Press Ctrl+C to stop.

- Do not add `--reload` when using the serial port; reloading can leave the port locked.
- `DEMO_HISTORY=1` fills the weekly and monthly pages with **simulated** history (clearly labelled on screen) so the aging analysis has something to display. Live readings are real.
- If `model.joblib` exists, the dashboard shows "Detector: ML model". Otherwise it uses simple rules (see section 8).

Command Prompt equivalents (instead of `$env:`):
```
set SERIAL_PORT=COM3
set DEMO_HISTORY=1
python -m uvicorn main:app --port 8000
```

### Test without hardware
```
cd backend
$env:DEMO="1"
python -m uvicorn main:app --port 8000
```
This generates fake live readings that cycle through normal, dirty, partial shading and open circuit, plus simulated history.

PowerShell keeps `$env:` values until the terminal is closed. After a fake-data test, open a new terminal (or run `Remove-Item Env:DEMO`) before starting the real dashboard, otherwise fake readings mix with real ones.

### Optional shortcuts (.bat files)
`setup.bat`, `collect.bat <label> [rows]`, `train.bat`, `run_dashboard.bat`, `run_demo_fake.bat` run the same commands. In PowerShell, use `.\collect.bat normal 100`. Edit COM3 inside them if needed.

---

## 7. The dashboard

| Section | What it shows |
|---|---|
| Status chip and banner | Current detected state, colour-coded, with a suggested action (for example "clean the panel") |
| KPI cards | Voltage, current, power, light, temperature, performance ratio, energy today |
| Live chart | Power and light over the last 120 readings |
| Health card | Panel health (recent performance ratio versus the first 7 days) and a trend in percent per month |
| Daily / Weekly / Monthly | Energy (bars) and performance ratio (line); weekly shows best and worst day; monthly shows month-over-month change |
| Fault alerts | Fault episodes with start time, last seen time and suggested action |
| Sound alerts button | Click once to enable a beep on new faults (browsers block audio until you click). The tab title also shows a warning |

Weekly and monthly pages show a "SIMULATED" note whenever simulated history is included.

---

## 8. How fault detection and aging analysis work

### Expected power and performance ratio
```
expected_power = K_P × lux × (1 − 0.004 × (temperature − 25))
performance_ratio (PR) = measured_power / expected_power
```
`K_P` is learned from your `normal` data. The temperature term accounts for the usual drop in panel output as it gets hotter. Comparing PR instead of raw power keeps a cloudy day from looking like panel aging.

### Model features
`lux, temp_c, voltage_v, current_ma, power_mw, pr`

### Rule-based fallback (used until `model.joblib` exists)
1. `lux < LOW_LUX` → `low_light`
2. current below 0.5 mA with voltage above 2 V → `open_circuit`
3. PR ≥ 0.85 → `normal`
4. PR ≥ 0.55 → `dirty`
5. otherwise → `partial_shading`

These thresholds are rough placeholders. The trained model replaces them.

### Suggested actions
- `partial_shading`: check for shadows or objects covering part of the panel
- `dirty`: output is lower than the light predicts, clean the panel
- `open_circuit`: voltage but no current, check wiring, connectors and the load
- `short_circuit`: disconnect and inspect wiring (defined in the code but not a trained class)
- `low_light`: reduced output is expected, no action
- `normal`: system healthy

### Energy, health and aging
- **Daily energy (Wh)** is the sum of power over time; gaps longer than 900 seconds are capped.
- **Daily PR** averages only rows classified `normal`, so faults do not distort it.
- **Health %** = average PR of the last 7 days divided by the average of the first 7 days.
- **Aging trend** = slope of a straight-line fit of daily PR, in percent per month. It needs at least 8 days with data.
- Real panels degrade only about 0.5 to 1% a year, so real aging cannot be seen in a short project. The simulated history uses accelerated aging (about 1.2% a month) so the feature can be demonstrated, and it is always labelled as simulated.

---

## 9. Data storage and API

SQLite file `backend\solar.db`, table `readings`:
`id, string_id, ts, entry, lux, ldr, temp_c, voltage_v, current_ma, power_mw, label, confidence, pr, simulated`

Simulated history is seeded once when `DEMO` or `DEMO_HISTORY` is set and no simulated rows exist. Delete `solar.db` to start clean (stop the backend first).

CSV files:
- `solar_data.csv`: Clean labelled data (8 columns: timestamp,lux,ldr,temp_c,voltage_v,current_ma,power_mw,label) - deliverable, 1182 rows before final tests
- `rejected_solar_data.csv`: Debug artifact (timestamp,raw_line,reason,label) - gitignored, contains boot spam `00`, `mw`, `try 0x40...`, concatenated lines, `nan_in:temp_c`, `nan_in:voltage_v,current_ma,power_mw`, `parse_error`, `bad_field_count_{n}`. Grows on every run, header appears once.

API endpoints (interactive docs at http://127.0.0.1:8000/docs):

| Endpoint | Returns |
|---|---|
| `GET /api/latest` | Newest reading, detected state, advice, energy today, detector type |
| `GET /api/readings?limit=120` | Recent readings for the live chart |
| `GET /api/daily?days=30` | Per-day energy, peak power, PR, fault count |
| `GET /api/weekly` | Per-week totals, best and worst day |
| `GET /api/monthly` | Per-month totals and month-over-month PR change |
| `GET /api/health` | Health %, aging trend |
| `GET /api/alerts` | Recent fault episodes |

Configuration (environment variables): `SERIAL_PORT`, `DEMO`, `DEMO_HISTORY`, `STRING_ID` (default `string1`), `K_P`, `LOW_LUX` (override `calib.json`). The code also still contains an optional ThingSpeak poller (`TS_CHANNEL`, `TS_READ_KEY`); it is unused in this USB version.

---

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `could not open port COM3` / access denied / `Could not open port /dev/ttyUSB0 — is another program using it?` | Another program has the port. Close Serial Monitor, the logger and the dashboard. Check COM number in Arduino IDE. Run `sudo fuser -k /dev/ttyUSB0` on Linux. Logger now prints friendly message with tip, no traceback. |
| `Serial read failed — port was grabbed or device unplugged?` | USB unplugged mid-run or another program grabbed port. Logger prints `Port X closed.` and exits cleanly. Re-plug and re-run. |
| Dashboard empty | Terminal should say `Serial connected`. Press the ESP32 EN button; check the sketch prints CSV lines with `# FIRMWARE_VERSION=1` |
| `# ERROR: INA219 not found` or BH1750 not found | Check SDA/SCL (21/22), power on 3V3 and ground. Logger now prints these ERROR lines to console immediately (Phase 2). |
| `# ERROR: DS18B20 not found` + `rejected (nan_in:temp_c)` + 0 rows saved | DS18B20 disconnected or missing 4.7kΩ pull-up. Check GPIO4 wiring. Rows go to `rejected_solar_data.csv` with reason `nan_in:temp_c`. Example rejected row: `"21.6,1349,nan,0.002,0.00,0.00","nan_in:temp_c",normal`. Note: `--rows 2` means 2 GOOD rows, so when all rows rejected, logger loops forever — use `timeout 6` or Ctrl+C. |
| `# ERROR: INA219 not found` + `rejected (nan_in:voltage_v,current_ma,power_mw)` | INA219 disconnected. Reason contains 3 names in HEADER order, proves zip fix works. Example: `"25.2,1310,25.38,nan,nan,nan","nan_in:voltage_v,current_ma,power_mw",normal` |
| `rejected (bad_field_count_1): 00` or `mw` or `try 0x4008059c` | Normal ESP32 boot spam at 115200. Filtered by logger to `rejected_solar_data.csv`, clean CSV unaffected. |
| `WARNING: No # FIRMWARE_VERSION line seen before first data row...` at row 1 | Expected for runs <30s. Firmware prints version at boot + every 30s (millis non-blocking). Logger prints warning once at row 1 if missed boot print, then `Detected # FIRMWARE_VERSION=1` when 30s re-print arrives. Final line: `Firmware: UNKNOWN` for short run, `Firmware: 1` for long run (>30s). Not a bug. |
| `Detected # FIRMWARE_VERSION=1` appears after row ~10 | Normal — 30s periodic re-print caught by logger. Version uses `millis()` rolling timer, not `delay(30000)`, so CSV cadence stays ~2s. |
| Voltage near 0 and tiny current (0.001V) | Panel in the dark, loop not complete, or panel − not on common GND. In final tests, 0.001V indicates low light indoor test. |
| Voltage or current looks wrong | Check VIN+ → VIN- is through module, not jumpered; 33 Ω resistor is out |
| ESP32 resets / `Brownout detector was triggered` | Use short, good USB cable in direct laptop port. Avoid C-to-C cable, use USB-A to C. |
| `python is not recognized` | Reinstall Python with "Add to PATH", or try `py` instead of `python`, `python3` on Linux |
| `database is locked` | Move project out of OneDrive; run only one backend |
| Labels look wrong on real data before training | Expected: rule thresholds are placeholders. Collect data and run `train.py` |
| Labels still look wrong after training | Check for mislabelled rows, changed load, or BH1750 not beside panel. Check `rejected_solar_data.csv` for hidden faults. Re-collect class |
| Fake readings mixed with real | `DEMO` still set in terminal. Use new terminal |
| `.bat` double-click window closes at once | `collect.bat` needs label; run from terminal |
| `Interrupted by user (Ctrl+C)` + `Port X closed.` | Normal Phase 2 behavior - Ctrl+C now prints message + guaranteed close + second run works |
| `rejected_solar_data.csv` has 1 line after `rm -f` + `--rows 2` | If all rows rejected (e.g., DS18B20 disconnected), `--rows` counts only GOOD rows, so logger never finishes. Use `timeout 6` or Ctrl+C. wc -l will be 1 header + N rejects, grep timestamp =1. Header integrity PASS if timestamp count =1. |

---

## 11. Limitations (state these honestly in the report)

- Small prototype panel, tested over a short time on one setup. Accuracy results are prototype-level and depend on the conditions you recorded.
- The model learned your specific panel, load and sensor placement. Changing any of them needs new data.
- Weekly and monthly history is simulated. Only data recorded after you start the system is real.
- `dirty` and `partial_shading` can look similar; expect the most confusion between them.
- Real aging is too slow to observe in this project.
- The "string" concept (many panels on one ESP32) is a design extension, not implemented. In a series string one current sensor can detect a fault but cannot say which panel caused it; per-panel voltage sensing or several strings would be needed.
- No Wi-Fi, cloud or remote access in this version.
- Logger's `rejected_solar_data.csv` is a debug artifact that grows on every run - gitignored, not a deliverable. When all rows are rejected (sensor disconnected), `--rows N` never completes because it counts only good rows — use timeout or Ctrl+C.
- Firmware version warning at row 1 for short runs (<30s) is expected — version print is every 30s rolling, final `Firmware: 1` confirms correct firmware after long run.

## 12. Possible future work
- Wi-Fi upload (HTTP or MQTT) to a cloud backend or ThingSpeak
- Several panels or strings with per-string IDs and per-panel voltage sensing
- Email or phone notifications for faults
- Long-term outdoor logging to measure real degradation
- Running a small model on the ESP32 itself
- Improve rejected-file analysis: auto-classify boot spam vs sensor faults vs schema mismatch
- Add --stats flag to logger.py to summarize rejected reasons

## 13. Phase 2 Implementation Notes (Tasks 1-10) — Final Verification

**Firmware changes (solar_serial.ino):**
- Added CSV Contract + Known Limitations comment blocks
- Added `Serial.println("# FIRMWARE_VERSION=1");` in setup() before header + every 30s in loop() using `millis()` non-blocking (NOT delay), rolling timer
- Global `unsigned long lastVersionPrint = 0;` + `if (millis() - lastVersionPrint >= 30000UL) { println; lastVersionPrint = millis(); }`
- Added DS18B20 check with `getDeviceCount() == 0` -> `# ERROR: DS18B20 not found`
- Fixed INA219 fault to output NAN not 0 for proper fault detection: `v=NAN;i=NAN;p=NAN` when inaOK false
- Comment above printf: Must match CSV header - increment FIRMWARE_VERSION if changed
- Verified: Serial Monitor shows version at boot, then every 30s, CSV cadence stays ~2s

**Logger changes (tools/logger.py Tasks 1-10) — Final zip-safe version:**
```python
# Task 1: REJECTED_HEADER
REJECTED_HEADER = ["timestamp","raw_line","reason","label"]

# Task 2: rejected file path same dir as --file
rejected_file_path = os.path.join(os.path.dirname(args.file), f"rejected_{basename}")

# Task 3: Dual open with header check
with open(args.file, "a", newline="") as f, open(rejected_file_path, "a", newline="") as rf:
    w = csv.writer(f)
    rw = csv.writer(rf)
    if new_file: w.writerow(HEADER)
    if new_rejected_file: rw.writerow(REJECTED_HEADER)

# Task 4: Issue 1 fix — zip-safe nan handling
nan_cols = []
for col_name, v in zip(HEADER[1:-1], vals):  # safe, stops at shorter, no IndexError
    if math.isnan(v):
        nan_cols.append(col_name)
if len(vals) != len(HEADER)-2:
    reason = f"schema_mismatch_{len(vals)}vs{len(HEADER)-2}"
    rw.writerow([...]); continue
if nan_cols:
    reason = f"nan_in:{','.join(nan_cols)}"  # e.g. nan_in:temp_c or nan_in:voltage_v,current_ma,power_mw
    rw.writerow([...]); continue

# Task 5: Print # ERROR/WARNING
if line.startswith("#"):
    if line.startswith("# ERROR") or line.startswith("# WARNING"): print(line)
    if line.startswith("# FIRMWARE_VERSION="): saw_firmware_version = True ...

# Task 6: Version soft-check
saw_firmware_version flag, Detected print, warning at count==1 if missing, final Firmware: 1/UNKNOWN

# Task 7: Graceful port open
try: ser = serial.Serial(...)
except SerialException as e: print(f"Could not open port {args.port} — is another program using it?") + details + tip + sys.exit(1)

# Task 8: Guaranteed close
try: main loop
except KeyboardInterrupt: print("Interrupted by user (Ctrl+C)")
finally: if ser.is_open: ser.close(); print(f"Port {args.port} closed."); print(f"Done. {count} rows saved. Firmware: ...")

# Task 9: .gitignore
rejected_solar_data.csv
rejected_*.csv

# Task 10: Docstring with B.2-B.8
```

**Verification (12 tests — Final):**

| Test | Name | Result | Evidence |
|---|---|---|---|
| 1 | Happy path | PASS (functional) | `1/5 35.9,1358...` +5 rows, 0 rejected, 8 fields. Warning at row1 expected for <30s run. Long run >30s shows `Detected # FIRMWARE_VERSION=1` + `Firmware: 1` |
| 2 | DS18B20 disconnect | PASS | `# ERROR: DS18B20 not found` + `rejected (nan_in:temp_c): 21.6,1349,nan,0.002,0.00,0.00` + first rejected row `"21.6,1349,nan,0.002,0.00,0.00","nan_in:temp_c",normal` + 0 saved + Firmware: 1 |
| 3 | INA219 disconnect | PASS — proves Issue 1 fix | `rejected (nan_in:voltage_v,current_ma,power_mw): 25.2,1313,25.31,nan,nan,nan` + first row `"25.2,1310,25.38,nan,nan,nan","nan_in:voltage_v,current_ma,power_mw",normal` — 3 names HEADER order |
| 4 | Malformed line | PASS by inspection | bad_field_count_{n} + parse_error branches present, write to rejected, continue |
| 5 | Port busy | PASS | `Could not open port /dev/ttyUSB0 — is another program using it?` + Details + Tip + exit 1 no traceback |
| 6 | Nonexistent port | PASS | Similar friendly message for /dev/ttyUSB99 + exit 1 |
| 7 | Ctrl+C | PASS | `Interrupted by user (Ctrl+C)` + `Port /dev/ttyUSB0 closed.` + `Done. X rows saved.` + second run works |
| 8 | USB unplug | PASS | `Serial read failed — port was grabbed or device unplugged?` + `Port closed.` + clean exit + re-run works |
| 9 | Late-start version | PASS — Firmware: 1 | Short run (<30s): warning at row1 + UNKNOWN. Long run (>30s): `Detected # FIRMWARE_VERSION=1` + `Firmware: 1`. Confirms millis non-blocking, CSV ~2s cadence preserved |
| 10 | Version mismatch | PASS by inspection | Code path `if firmware_version_str != "1" and != ""` warns correctly |
| 11 | Rejected header integrity | PASS | `rm -f`, run, `wc -l 1`, `grep -c timestamp 1` header once. Second run `wc -l 9`, `grep -c timestamp 1` — no duplication |
| 12 | Main CSV unchanged | PASS | 8 fields old and new, timestamp ISO identical, label present, no blank line |

**Key findings:**
- Issue 1 fix `zip(HEADER[1:-1], vals)` verified by Test 3 multi-field reason in correct order
- Firmware 30s `millis()` print verified: non-blocking, CSV ~2s, version appears after 30s, final `Firmware: 1`
- Warning at row 1 for short runs is expected, not a bug — prints once via `version_warning_printed` flag
- When all rows rejected, `--rows` never completes (counts only good rows) — use `timeout 6` or Ctrl+C, this is correct
- `.gitignore` confirmed: `rejected_solar_data.csv` + `rejected_*.csv` only, NOT solar_data.csv
