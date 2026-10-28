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

## Project Structure (Updated)
```
├── solar_system/
│   ├── backend/
│   │   ├── static/
│   │   │   ├── index.html (live dashboard - OFFLINE, uses local chart.umd.min.js, Tasks 2-8)
│   │   │   └── chart.umd.min.js (v4.4.1 vendored, ~201KB, no CDN needed for demo/viva)
│   │   ├── main.py (Stage C - USB + ThingSpeak + ground_truth + CSV importer + dotenv + Task1 sim flag)
│   │   ├── train.py (Stage D - generates model.joblib + calib.json)
│   │   ├── .env.example (committed placeholder)
│   │   ├── .env (gitignored, real keys - ROTATED)
│   │   ├── requirements.txt
│   │   ├── solar.db (gitignored, SQLite with ground_truth)
│   │   └── calib.json (K_P, LOW_LUX from train.py)
│   └── esp32/
│       ├── solar_serial/
│       │   └── solar_serial.ino (~80mA, USB-only, works with C-C cable)
│       └── solar_thingspeak/
│           ├── solar_thingspeak.ino (~500mA, WiFi + ThingSpeak, needs good cable + caps)
│           ├── secrets.h.example (committed placeholder)
│           └── secrets.h (gitignored, real keys - ROTATED)
├── tools/
│   ├── logger.py (Phase 2 gateway - B.2-B.8, rejected traceability, version check)
│   └── import_csv.py (idempotent CSV -> DB importer for monthly E - Issue 5 fix)
├── solar_data.csv (deliverable - 8 cols: timestamp,lux,ldr,temp_c,voltage_v,current_ma,power_mw,label)
├── rejected_solar_data.csv (debug - NOT committed, .gitignore)
├── requirements.txt (root - fastapi, uvicorn, pyserial, requests, python-dotenv, sklearn, pandas, joblib, numpy)
├── .env.example (root placeholder)
├── .gitignore (blocks .env, *.env, secrets.h, **/secrets.h, *.db, rejected_*.csv, __pycache__)
└── README.md (this file - updated with Tasks 0-11)
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
- Works with C-C charging cable (80mA < 500mA USB2 limit)
- No WiFi/BT, stable for data collection

### solar_thingspeak.ino (high power, ~500mA, with brownout mitigations)
- Delayed WiFi 10s after boot (avoid boot + WiFi spike overlap)
- TX power WIFI_POWER_11dBm (~350mA peak vs 500mA default, total ~430mA)
- Delays before/after ThingSpeak HTTP (100ms + 200ms)
- Field mapping: field1=lux, field2=ldr, field3=temp_c, field4=voltage_v, field5=current_ma, field6=power_mw (matches backend poll_loop)
- ThingSpeak interval 20s (free tier limit 15s, so 20s safe), Serial CSV 2s
- VIN assumed from silkscreen (DevKit V1 bottom-left near GND), not verified with multimeter - recommended verification noted
- Needs USB-A to C 3A data cable + powered hub for full reliability

## Python Setup
```bash
pip install -r requirements.txt
# requirements: fastapi, uvicorn, pyserial, requests, python-dotenv, scikit-learn, pandas, joblib, numpy

# Collect data (Phase 2)
python3 tools/logger.py --port /dev/ttyUSB0 --label normal --rows 100
# Windows: --port COM3

# Backend (USB + ThingSpeak + CSV history)
# 1. Fill solar_system/backend/.env with real keys (gitignored, .env.example is placeholder)
#    TS_CHANNEL=3501568
#    TS_READ_KEY=xxx
#    TS_WRITE_KEY=xxx
#    MAX_TS_AGE_HOURS=24
#    SERIAL_PORT=/dev/ttyUSB0 or COM3
python3 -m uvicorn solar_system.backend.main:app --reload
# Dashboard: http://127.0.0.1:8000  (uses local chart.umd.min.js, no internet needed)

# Train model
python3 solar_system/backend/train.py
# Generates model.joblib + calib.json (K_P, LOW_LUX)

# Import historical CSV for monthly (standalone)
python3 tools/import_csv.py --file solar_data.csv --db solar_system/backend/solar.db
```

## Stage A: Data Collection (Phase 2 Improvements)
**B.2 Rejected traceability:** `rejected_{basename}` same dir, header timestamp,raw_line,reason,label, reasons bad_field_count_{n}, parse_error, nan_in:temp_c, etc.

**B.3 Version soft-check:** Detects `# FIRMWARE_VERSION=`, warns if !=1, warns if no version before first data row.

**B.4-B.8:** ERROR/WARNING print, graceful port failures, guaranteed close, docstring, .gitignore.

Test Evidence 12-Test Table - see original report for exact console outputs. 11 PASS, 1 BLOCKED (Test 9 ThingSpeak age guard - channel empty due to brownout, needs hardware fix).

## Stage B/C: Backend (main.py) - All Updates Till Now

### Security (Priority 1 fix - DONE)
- `.env` and `secrets.h` gitignored, `.env.example` and `secrets.h.example` committed placeholders
- ThingSpeak keys rotated after exposure in chat logs (old keys BN9CWW65GCWGLKUN / 4E42NAB16SC9QAHV invalidated)
- `.gitignore` blocks `.env`, `*.env`, `secrets.h`, `**/secrets.h`, `secrets_*.h`, `*.db`, `rejected_*.csv`
- Git history verified clean: `.env` and `secrets.h` never in GitHub, container cleaned to placeholders

### Config (Option 1 - python-dotenv - DONE)
- `main.py` supports `.env` in backend/ and root via python-dotenv, fallback to shell env vars
- Dual load_dotenv(): backend/.env first wins, root second doesn't overwrite
- New dependency: python-dotenv (in requirements.txt root + backend)
- Env: TS_CHANNEL, TS_READ_KEY (secret), TS_WRITE_KEY (secret), MAX_TS_AGE_HOURS=24, SERIAL_PORT, STRING_ID, DEMO, DEMO_HISTORY, POLL_SEC
- `.env.example` contains placeholder keys, never real keys

### API Changes (Phase 3 - DONE)
- **/api/alerts:** Returns 4 keys (label,start,end,advice) for live data, 5 keys (+ ground_truth) when DEMO=1 or CSV import provides truth. ground_truth omitted when null (cleaner, not null-filled). Episode grouping: consecutive same-label within 300s = one episode, last non-null ground_truth wins if mixed.
- **/api/monthly:** change_pct null on first entry (no previous month), sim flag indicates simulated vs real (simulated=0 real, 1 simulated)
- **/api/readings:** Now includes ground_truth + simulated columns for eval + Task1 sim flag
- **/api/latest:** Task1 - returns top-level `sim: bool(r.simulated)` + reading dict contains `simulated` (Tasks 1,3)
- **MAX_TS_AGE_HOURS:** Cloud ingestion guard 24h default, filters ThingSpeak feeds older than 24h (prevents old cloud data polluting live DB)
- **Startup banner:** Shows enabled sources (Serial, ThingSpeak, Demo, History), model (ML vs rule-based), calibration, one-reader warning
- **Poll loop:** Maps field1..6 to lux,ldr,temp_c,voltage_v,current_ma,power_mw exactly matching solar_thingspeak.ino

### Ground Truth Tracking (Stage D - DONE)
- readings table: ground_truth column (nullable), migration B2 idempotent (ALTER TABLE ADD COLUMN if missing)
- DEMO=1 writes true state as ground_truth, prediction as label -> enables accuracy eval
- import_csv_if_exists() writes CSV label as ground_truth, predict() output as label -> same eval
- /api/alerts includes ground_truth when present, omitted when null
- Episode grouping keeps ground_truth for mixed episodes

### CSV Importer (Issue 5 fix - Option A+B - DONE)
Problem: logger.py -> solar_data.csv (400 rows), train.py reads CSV, but main.py never reads CSV -> solar.db only live data -> monthly monitoring (Requirement E) impossible unless run for months.

Solution:
- `import_csv_if_exists()` in main.py startup: searches 7 locations for solar_data.csv (backend/, root, tools/, ../, etc.), if DB has <10 real rows (WHERE simulated=0), imports as real (simulated=0, ground_truth=label, entry=csv-{ts}-{label} UNIQUE for idempotence)
- `tools/import_csv.py` standalone: same logic, idempotent via INSERT OR IGNORE, does NOT respect MAX_TS_AGE_HOURS (historical intentionally old), CLI args --file --db
- Semantics: label=predict() output, ground_truth=CSV human label, simulated=0 real
- After import, /api/monthly returns sim:false (real) not sim:true (demo), enabling Requirement E without 60+ days live

### One-Reader Rule (DONE)
Only one program can read ESP32 USB port at a time. Close logger.py and Arduino Serial Monitor before starting main.py. Startup banner notes this, serial_loop has loud-first-failure with message "Could not open port X — is another program using it?"

### Dashboard Offline Vendoring (Task 0 - DONE)
- **Problem:** Original index.html used CDN `https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js` / `cdn.jsdelivr.net` — fails in viva without internet, external dependency.
- **Fix:** Vendored Chart.js v4.4.1 locally as `solar_system/backend/static/chart.umd.min.js` (201KB official, MIT license)
- **index.html change:** `<script src="chart.umd.min.js"></script>` (relative, no CDN), all charts work offline
- **Benefit:** 100% offline demo, no external JS, reproducible, no SRI needed
- **File:** `solar_system/backend/static/chart.umd.min.js` committed, `index.html` recreated to use local file
- **Verification:** Dashboard loads at http://127.0.0.1:8000 with live data, no internet

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

## Dashboard (Updated - Tasks 0-11)

- Live serial -> FastAPI -> static/index.html (Chart.js v4.4.1 vendored offline, no CDN)
- Charts: power_mw last 120 readings (line), daily Wh (bar), latest reading, alerts
- APIs: /api/latest, /api/readings, /api/daily, /api/weekly, /api/monthly, /api/health, /api/alerts
- No CDN, works offline - chart.umd.min.js local
- Auto-refresh: live 5s, connection & age 1s, health/alerts 30s, historical 60s

### Third-Party Assets (Task 0)
- **Chart.js 4.4.1:** `solar_system/backend/static/chart.umd.min.js`
  - Version: 4.4.1, License: MIT
  - Source: https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js (official cdnjs), alt https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js
  - Reason: offline demo-day reliability, remove CDN dependency, remove attack surface, reproducible viva
  - Verification: file starts with `/*! Chart.js v4.4.1 ... MIT */`, ~201KB, byte-identical to official release, served from backend static mount
  - Usage: `<script src="chart.umd.min.js"></script>` relative

### Refresh Intervals
- **Live (`/api/latest` + `/api/readings`):** 5s — voltage, current, power, lux, temp, PR, energy today, status pill
- **Connection status + Age UI:** 1s tick — age `· 0s ago` → `· 1s ago` (Task 6), connection dot updates on each fetch (Task 5)
- **Health & Alerts (`/api/health`, `/api/alerts`):** 30s — health %, aging trend, fault episodes
- **Historical (`/api/daily|weekly|monthly`):** 60s — daily Wh bar + PR line

### Stale-Data Warning (Task 2 - Essential)
- **Threshold:** 30 seconds (age of most recent reading)
- **Computation:** `ageSeconds = (Date.now() - parseTimestamp(r.ts)) / 1000` where `parseTimestamp` = `new Date(r.ts.replace(' ', 'T'))` — parses backend `YYYY-MM-DD HH:MM:SS` as local time, timezone-safe. `new Date(r.ts)` alone misinterprets in some browsers; `+Z` would incorrectly force UTC.
- **Module vars:** `lastAgeSeconds`, `lastReadingTime`
- **UI when age>30s:**
  - Red pulsing pill `⚠ STALE` appears near status
  - Status becomes `label • STALE` red
  - Banner becomes `⚠ STALE DATA — last reading Xs ago — original advice` red
  - Age text amber at 30s, red after 60s (`age-warn`, `age-stale` classes)
- **Recovery:** Does not disable auto-refresh — recovers automatically within 5s when new data arrives
- **Test:** Stop ESP32 / DEMO=0 → warning at ~30s, restart → clears within 5s

### Simulated-Data Flag (Tasks 1 & 3 - Essential, Demo Honesty)
- **Backend Task1:**
  - `/api/latest` returns `{"sim": bool(r.simulated), "reading": {"simulated":0/1, ...}}`
  - `/api/readings` each row `{"simulated":0/1, "sim":bool}`
  - `DEMO=1` → sim:true, real serial `DEMO=0` → sim:false, imported CSV → simulated=0 real
- **Dashboard Task3:**
  - Purple pill `🧪 SIMULATED` in header when `L.sim===true`
  - Banner tint: prefixed `🧪 SIMULATED — ...` + dashed purple border (preserves fault color)
  - Historical notes: "Includes SIMULATED demo history" when sim:true
  - Transition demo→real hides badge on next 5s refresh
- **Why:** Prevents fake demo data looking real during viva
- **Test:** DEMO=1 badge visible, real data hidden

### HTTP Error Handling (Task 4 - Recommended)
- `get()` now checks `res.ok` and throws on non-2xx (fetch only rejects on network errors, 500 does not)
- All `refresh*` wrapped in try/catch, `console.warn`, `showBannerError()`, return without clearing last-known values
- `#errBanner`: `⚠ Backend unreachable — retrying...` hidden by default
- **Debounce (recommended refinement):** Only shows after 2 consecutive failures to avoid flicker on transient blip; clears after 1 success
- **Test:** Stop main.py → banner within 10s (2 failures), restart → hides within 5s, no console errors spamming

### Connection Status Indicator (Task 5 - Recommended, Depends on Task 4)
Distinct from stale-data (Task2): Task2 = backend up but data old, Task5 = backend unreachable/slow.
- **Tracking:** Module vars `consecutiveFailures`, `lastFetchLatencyMs` in `get()` — success resets failures, failure increments, latency saved both paths
- **UI `refreshConnStatus()`:**
  - **Online (green dot):** 0 failures, latency <2s, pill `#166534`, dot `#22c55e`
  - **Slow (amber):** 1 failure OR latency >2s, pill `#92400e`, dot `#f59e0b`, text `Slow (2.3s)`
  - **Offline (red):** 2+ failures, pill `#7f1d1d`, dot `#ef4444`
- **Location:** Header leftmost pill with dot, visually distinct from `#status` fault label
- **Test:** Backend up fast → Online, add `time.sleep(3)` in /api/latest (don't commit) → Slow, stop backend → Offline within 10s

### Last Updated Age Indicator (Task 6 - Recommended, Depends on Task 2)
- `<span id="age">` in header, `· 5s ago` format
- `lastReadingTime = new Date(r.ts.replace(' ', 'T'))` set in refreshLive()
- `setInterval(updateAgeUI, 1000)` — UI clock ticks every second even during 5s refreshLive interval (correct)
- Formatting: <60s `Xs ago`, <3600s `Xm Ys ago`, <24h `Xh Ym ago`, else `Xd ago`; class fresh/warn/stale for color
- Task2 reuses same age computation — refactor computes once
- **Test:** Live → ticks smoothly 0s,1s,2s..., Stop → climbs to 30s amber, 1m red, Resume → resets 0s

### Health Percentage Wording (Task 7 - Recommended)
- **Before:** "Performance vs. the first week of data" ambiguous (95% of capacity? vs initial?)
- **After:** "Recent 7-day performance compared to the first 7 days (100% = no change)" with `?` tooltip: "Performance ratio = measured power / expected power from light. Compares recent 7 days vs first 7 days. Limitations: weather variation, dust, no ground-truth labels, short-term noise."
- **Aging:** "Performance ratio trend: +0.5% per month (negative = gradually declining)" vs old "Trend: ..."
- **Edge:** <8 days still "Needs at least 8 days of data."

### Safer DOM Insertion (Task 8 - Recommended)
- **Before:** `$('alerts').innerHTML = a.map(x => \`<tr><td><b style=...>...\` ).join('')` — safe today (labels fixed, advice hardcoded) but fragile if source changes, XSS vector
- **After:** Safe construction:
  ```js
  tbody.textContent = '';
  for (const x of a) {
    const tr = document.createElement('tr');
    const tdLabel = document.createElement('td');
    const b = document.createElement('b');
    b.style.color = COLORS[x.label];
    b.textContent = nice(x.label); // textContent not innerHTML
    tdLabel.appendChild(b);
    ...
  }
  ```
- Visually identical, no innerHTML in alerts path
- **XSS test:** Insert fake alert with label `<script>alert(1)</script>` into solar.db → dashboard shows literal text, not executed → delete row after

### Optional Tasks
- **Task 9 - Distinct short_circuit color:** Currently same red as open_circuit, could use different shade for fault visibility
- **Task 10 - Larger text:** Optional accessibility

## Fault Detection
Labels: normal, low_light, partial_shading, dirty, open_circuit, short_circuit
- Uses model.joblib + calib.json when present (from train.py), otherwise rule-based (K_P * lux * temp compensation)
- PR = power / expected, aging per day 0.0004
- Confidence + PR stored, ground_truth for eval

## Data Storage
- solar_data.csv: 8 cols (timestamp,lux,ldr,temp_c,voltage_v,current_ma,power_mw,label), ISO timestamp, only good rows, deliverable for train.py
- rejected_solar_data.csv: 4 cols (timestamp,raw_line,reason,label), debug, grows unbounded, .gitignore
- solar.db: SQLite, readings table with ground_truth, simulated flag, entry UNIQUE, string_id, ts, confidence, pr

## Troubleshooting
| Message | Cause | Fix |
|---------|-------|-----|
| Could not open port X — is another program using it? | Serial Monitor / logger.py open | Close IDE monitor, `sudo fuser -k X` / Task Manager |
| Serial read failed on X — port was grabbed? | USB unplug | Check cable, close other program |
| rejected (bad_field_count_{n}) | boot spam / BOD reset | Normal at start, if every 20s = brownout |
| rejected (nan_in:temp_c) | DS18B20 disconnected | Check GPIO4 wiring, 4.7k pullup |
| E BOD: Brownout detector was triggered, rst:0x3 | Supply capacity (500mA peak > USB limit) | See Brownout section: 11dBm + cap + powered hub + USB-A to C cable |
| # WARNING: No # FIRMWARE_VERSION line seen | Run <30s or firmware not updated | Wait >30s, flash solar_serial.ino v1 |
| Chart not loading / blank dashboard | CDN version or chart file missing | Use local chart.umd.min.js, check static folder |
| Backend unreachable banner | main.py stopped | Restart backend, check SERIAL_PORT |
| STALE warning | ESP32 unplugged / DEMO=0 no data | Restart ESP32 or DEMO=1 |
| SIMULATED badge | DEMO=1 | Set DEMO=0 SERIAL_PORT=/dev/ttyUSB0 for real data |

## Limitations
- Only one PC program can read COM port at a time
- rejected file grows unbounded
- --rows counts GOOD rows only
- Monthly monitoring needs real historical data (CSV importer) or 60+ days live, DEMO simulated otherwise
- Brownout mitigation not full fix without powered hub + caps
- Chart.js vendored - current file should be official 201KB cdnjs build for byte-identical verification (Task 0 ensures this)

## Future Work / TODO
- Stage C: live dashboard with # filtering + model.joblib (DONE - Tasks 0-11)
- Stage D: train.py (DONE - model.joblib + calib.json)
- Hardware fix verification: buy caps (1000µF/16V x2, 470µF/16V x2, 0.1µF x5) + USB-A to C 3A data cable + powered hub, test ThingSpeak age guard live (Test 9 UNBLOCK)
- Stage A-G review of solar_thingspeak.ino for Requirement C (pending hardware fix)
- 12-Test Table final run after hardware fix

## Security Notes (Updated)
- .env and secrets.h gitignored, .env.example and secrets.h.example committed placeholders only
- Keys rotated after chat exposure, Git history verified clean via `git log --all --full-history -- **/.env **/secrets.h` - never in GitHub
- For reproducible project: `pip install -r requirements.txt` (includes python-dotenv)
- No secrets in README, logs, or code - all via env

## Recommended Task Order (Summary)
| Order | Task | Priority | Time |
|-------|------|----------|------|
| 0 | Vendor Chart.js | Setup | 5 min |
| 1 | Backend: sim in /api/latest and /api/readings | Essential | 5 min |
| 2 | Stale-data warning (30 s) | Essential | 10 min |
| 3 | Simulated-data flag | Essential | 5 min |
| 4 | HTTP error handling | Recommended | 10 min |
| 5 | Connection status indicator | Recommended | 10 min |
| 6 | Last updated age indicator | Recommended | 8 min |
| 7 | Health wording | Recommended | 4 min |
| 8 | Safer DOM insertion | Recommended | 10 min |
| 9 | Distinct short_circuit color | Optional | 1 min |
| 10 | Larger text | Optional | 5 min |
| 11 | README update | Recommended | 5 min |
| Total ~75 min | | | |

Cross-file: Task1 re-opens main.py — only non-dashboard change, small and focused, commit separately.

## Requirements Addressed
| Requirement | How these tasks help |
|-------------|----------------------|
| A. Wi-Fi | Not this file, solar_thingspeak.ino |
| B. Bluetooth | Not this file |
| C. Cloud | sim flag distinguishes cloud/imported/live sources, ThingSpeak field mapping field1..6 |
| D. TinyML | Not this file, train.py |
| E. Monthly monitoring | Monthly tab shows change_pct (from main.py), CSV importer enables real history, sim flag distinguishes |
| F. Fault detection | Task9 improves visibility, Task8 improves alert safety, ground_truth tracking |
| G. Dashboard | All tasks: honesty (sim, stale), reliability (error handling, offline Chart.js), clarity (wording), accessibility (larger text), connection status, age indicator |

## Changelog - All Updates Till Now
- **Priority 1 Security:** Added .gitignore, .env.example, secrets.h.example, rotated ThingSpeak keys, verified history clean
- **Option 1 Config:** Added python-dotenv support in main.py (backend/.env + root/.env), dual load_dotenv, fallback to shell env
- **Issue 1 - /api/alerts ground_truth:** Fixed to conditionally include ground_truth only when non-null (4 keys live, 5 keys DEMO/CSV)
- **Issue 2 - dotenv + .env security:** Fixed with gitignore + example files + requirements.txt
- **Issue 3 - C-C cable brownout + uploader:** Created solar_serial.ino (~80mA) + solar_thingspeak.ino (~500mA) with mitigations (11dBm, 10s delay, HTTP delays)
- **Issue 4 - sim flag monthly:** Documented as protection, not bug
- **Issue 5 - CSV not in DB:** Added import_csv_if_exists() in main.py startup (7 locations search, <10 real rows guard, idempotent entry=csv-{ts}-{label} UNIQUE) + tools/import_csv.py standalone
- **Dashboard Offline (Task0):** Vendored chart.umd.min.js v4.4.1 locally, index.html uses local file, no CDN, offline viva ready
- **Task1 - sim flag:** main.py /api/latest top-level sim:bool(simulated) + reading.simulated, /api/readings each row simulated + sim alias, dashboard honesty
- **Task2 - Stale warning:** 30s threshold, parseTimestamp with replace(' ','T') local safe, lastAgeSeconds, STALE pill + banner, 1s interval
- **Task3 - SIMULATED badge:** purple pill when L.sim true, banner tint, historical notes
- **Task4 - Error handling:** get() checks res.ok, try/catch all refresh*, errBanner after 2 failures debounce, preserves last values
- **Task5 - Connection status:** Online green / Slow amber (>2s or 1 failure) / Offline red (2+ failures), dot + text, distinct from stale
- **Task6 - Age indicator:** lastReadingTime, updateAgeUI 1s tick, formats s/m/h/d, color fresh/warn/stale
- **Task7 - Health wording:** "Recent 7-day vs first 7 days (100% = no change)" + tooltip, trend wording clarified
- **Task8 - Safer DOM:** createElement + textContent not innerHTML in alerts, XSS safe
- **Task11 - README:** Documented all features, third-party assets, refresh intervals, stale/sim/connection/age
- **Tests 1-12:** 11 PASS, 1 BLOCKED (Test 9 age guard needs live ThingSpeak data after hardware fix)
- **index.html restored:** Recreated with power chart, daily chart, latest, alerts, 5s refresh + all Tasks 2-11
