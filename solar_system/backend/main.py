"""
Solar Monitor backend (FastAPI + SQLite), USB serial version.
Supports .env file in backend/ or project root (Option 1) via python-dotenv.
Normally started with run_dashboard.bat from the project folder.

Manual start (Command Prompt, in this folder):
    set SERIAL_PORT=COM3
    set DEMO_HISTORY=1          (simulated history for weekly/monthly pages, clearly labelled)
    python -m uvicorn main:app
Fake live data for testing without hardware:  set DEMO=1

Uses model.joblib + calib.json (made by train.py) when present, otherwise simple rules.

Phase 3 (Path B2): ground_truth column migration - idempotent & quiet
Stage D: ground_truth tracking for evaluation (DEMO=1 writes true label, prediction is label)

API behavior changes documented:
- /api/alerts returns 5 keys: label, start, end, advice, ground_truth?
  ground_truth present only when DEMO=1 or logger.py provided a label, otherwise null.
  Dashboard gracefully ignores unknown field (extra field safe for forward compat).
  Cross-file deps: backend/main.py -> readings table (ground_truth column) -> static/index.html (alerts UI)
- /api/monthly: change_pct null on first entry expected (no previous month)
  sim flag indicates simulated data (DEMO or seed_history) vs real (simulated=0)
  Monthly monitoring requirement E: needs real historical data for non-sim comparison

Tasks 1-7: .gitignore, MAX_TS_AGE_HOURS, startup banner, ground_truth CREATE, migration B2, one-reader notice, loud-first-failure

Env handling (Option 1):
- Requires python-dotenv (pip install python-dotenv) if using .env file
- Falls back to shell env vars via os.getenv if dotenv not installed
- .env must be gitignored, .env.example committed
- Contains TS_CHANNEL, TS_READ_KEY (secret), TS_WRITE_KEY (secret)

CSV import (Issue 5 fix, Option A+B):
- import_csv_if_exists() looks for solar_data.csv (from logger.py) in backend/, project root, tools/
- On startup, if DB has <10 real rows, imports CSV as real data (simulated=0, ground_truth=label)
- Satisfies monthly monitoring requirement E without DEMO
- If no CSV, DB only has live data from main.py start -> monthly needs long-term collection

C-C cable brownout (Issue 3):
- solar_serial.ino (no WiFi) ~80mA works with C-C charging cable
- solar_thingspeak.ino (WiFi) ~500mA needs good USB-A to C data cable + stable power
- Brownout causes rst cause 4, E BOD messages, filtered by B.2 rejection as bad_field_count
"""
import math
import os
import random
import sqlite3
import threading
import time
from datetime import datetime, timedelta
import json

# Load .env if present (Option 1) - new dependency python-dotenv
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"))
except ImportError:
    pass

import requests
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "solar.db")
TS_CHANNEL = os.getenv("TS_CHANNEL", "")
TS_READ_KEY = os.getenv("TS_READ_KEY", "")
STRING_ID = os.getenv("STRING_ID", "string1")
POLL_SEC = int(os.getenv("POLL_SEC", "60"))
MAX_TS_AGE_HOURS = float(os.getenv("MAX_TS_AGE_HOURS", "24"))
DEMO = os.getenv("DEMO", "0") == "1"
DEMO_HISTORY = os.getenv("DEMO_HISTORY", "0") == "1"
SERIAL_PORT = os.getenv("SERIAL_PORT", "")

_cal_file = os.path.join(BASE, "calib.json")
_cal = json.load(open(_cal_file)) if os.path.exists(_cal_file) else {}
K_P = float(os.getenv("K_P", _cal.get("K_P", 0.005)))
LOW_LUX = float(os.getenv("LOW_LUX", _cal.get("LOW_LUX", 3000)))
AGING_PER_DAY = 0.0004

FAULTS = {"partial_shading", "dirty", "open_circuit", "short_circuit"}
ADVICE = {
    "normal": "System healthy. Output matches the available light.",
    "low_light": "Low light, so reduced output is expected. No action needed.",
    "partial_shading": "Possible partial shading. Check for shadows or objects covering part of the panel.",
    "dirty": "Output is lower than the light level predicts. Possible dust or dirt: clean the panel.",
    "open_circuit": "Voltage present but no current. Check wiring, connectors and the load for a break.",
    "short_circuit": "Very low voltage with high current. Disconnect and inspect the wiring.",
}

lock = threading.Lock()

def run(sql, args=(), one=False, many=None):
    with lock:
        c = sqlite3.connect(DB)
        c.row_factory = sqlite3.Row
        try:
            if many is not None:
                c.executemany(sql, many)
                c.commit()
                return None
            cur = c.execute(sql, args)
            c.commit()
            rows = cur.fetchall()
            return (rows[0] if rows else None) if one else rows
        finally:
            c.close()

def ensure_schema():
    run("""CREATE TABLE IF NOT EXISTS readings(
        id INTEGER PRIMARY KEY AUTOINCREMENT, string_id TEXT, ts TEXT, entry TEXT UNIQUE,
        lux REAL, ldr REAL, temp_c REAL, voltage_v REAL, current_ma REAL, power_mw REAL,
        label TEXT, confidence REAL, pr REAL, simulated INTEGER DEFAULT 0,
        ground_truth TEXT)""")
    try:
        cols = run("PRAGMA table_info(readings)")
        col_names = [r["name"] for r in cols] if cols else []
        if "ground_truth" not in col_names:
            try:
                run("ALTER TABLE readings ADD COLUMN ground_truth TEXT")
                print("Migrated DB: added ground_truth column")
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                    pass
                else:
                    raise
    except sqlite3.OperationalError:
        pass

MODEL = None
try:
    import joblib
    if os.path.exists(os.path.join(BASE, "model.joblib")):
        MODEL = joblib.load(os.path.join(BASE, "model.joblib"))
        print("Loaded trained model.joblib")
except Exception as e:
    print("No ML model loaded:", e)

def predict(lux, temp, v, i, p):
    expected = K_P * lux * (1 - 0.004 * (temp - 25)) if lux > 0 else 0
    pr = p / expected if expected > 0 else 0.0
    if MODEL is not None:
        probs = MODEL.predict_proba([[lux, temp, v, i, p, pr]])[0]
        k = int(probs.argmax())
        return str(MODEL.classes_[k]), float(probs[k]), pr
    if lux < LOW_LUX:
        return "low_light", 0.8, pr
    if i < 0.5 and v > 2.0:
        return "open_circuit", 0.9, pr
    if pr >= 0.85:
        return "normal", 0.8, pr
    if pr >= 0.55:
        return "dirty", 0.6, pr
    return "partial_shading", 0.6, pr

INSERT = ("INSERT OR IGNORE INTO readings(string_id,ts,entry,lux,ldr,temp_c,voltage_v,current_ma,power_mw,"
          "label,confidence,pr,simulated,ground_truth) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)")

def add_reading(ts, entry, lux, ldr, temp, v, i, p, sim=0, ground_truth=None):
    label, conf, pr = predict(lux, temp, v, i, p)
    run(INSERT, (STRING_ID, ts, entry, lux, ldr, temp, v, i, p, label, conf, pr, sim, ground_truth))

def import_csv_if_exists():
    import csv
    candidates = [
        os.path.join(BASE, "solar_data.csv"),
        os.path.join(BASE, "..", "solar_data.csv"),
        os.path.join(BASE, "..", "..", "solar_data.csv"),
        os.path.join(os.path.dirname(BASE), "solar_data.csv"),
        os.path.join(BASE, "..", "tools", "solar_data.csv"),
        os.path.join(os.getcwd(), "solar_data.csv"),
        "solar_data.csv",
    ]
    csv_path = None
    for p in candidates:
        p = os.path.abspath(p)
        if os.path.exists(p):
            csv_path = p
            break
    if not csv_path:
        return 0
    real_count = run("SELECT COUNT(*) c FROM readings WHERE simulated=0", one=True)
    real_count = real_count["c"] if real_count else 0
    if real_count >= 10:
        return 0
    imported = 0
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    ts = row.get("timestamp") or row.get("ts") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        ts = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass
                    lux = float(row["lux"])
                    ldr = float(row.get("ldr", 2000))
                    temp = float(row["temp_c"])
                    v = float(row["voltage_v"])
                    i = float(row["current_ma"])
                    p = float(row["power_mw"])
                    label = row.get("label", "normal")
                    entry_id = f"csv-{ts}-{label}"
                    add_reading(ts, entry_id, lux, ldr, temp, v, i, p, sim=0, ground_truth=label)
                    imported += 1
                except (ValueError, KeyError, TypeError):
                    continue
        if imported > 0:
            print(f"Imported {imported} real rows from {csv_path} into solar.db (simulated=0, for monthly monitoring)")
        return imported
    except Exception as e:
        print(f"CSV import skipped ({csv_path}): {e}")
        return 0

def poll_loop():
    url = f"https://api.thingspeak.com/channels/{TS_CHANNEL}/feeds.json"
    params = {"results": 200}
    if TS_READ_KEY:
        params["api_key"] = TS_READ_KEY
    while True:
        try:
            feeds = requests.get(url, params=params, timeout=15).json().get("feeds", [])
            now = datetime.now()
            for f in feeds:
                try:
                    vals = [float(f[f"field{k}"]) for k in range(1, 7)]
                except (TypeError, ValueError, KeyError):
                    continue
                try:
                    ts_dt = datetime.fromisoformat(f["created_at"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
                except Exception:
                    continue
                if MAX_TS_AGE_HOURS > 0:
                    age_hours = (now - ts_dt).total_seconds() / 3600.0
                    if age_hours > MAX_TS_AGE_HOURS:
                        continue
                add_reading(ts_dt.strftime("%Y-%m-%d %H:%M:%S"), f"ts-{f['entry_id']}", *vals)
        except Exception as e:
            print("poll error:", e)
        time.sleep(POLL_SEC)

def serial_loop():
    import serial
    failure_count = 0
    while True:
        ser = None
        try:
            try:
                ser = serial.Serial(SERIAL_PORT, 115200, timeout=5)
            except serial.SerialException as e:
                failure_count += 1
                if failure_count == 1:
                    print(f"!!! Serial FAILED to open {SERIAL_PORT} — likely cause: port busy or not found")
                    print(f"    Details: {e}")
                    print(f"    Fix: Close Arduino Serial Monitor / logger.py, check cable, then: sudo fuser -k {SERIAL_PORT}")
                    print(f"    Will retry quietly (every 10th failure shown)...")
                elif failure_count % 10 == 0:
                    print(f"    Serial still failing ({failure_count}): {e}")
                time.sleep(3)
                continue
            print(f"Serial connected: {SERIAL_PORT}")
            failure_count = 0
            while True:
                try:
                    line = ser.readline().decode(errors="ignore").strip()
                except serial.SerialException as e:
                    print(f"Serial read failed on {SERIAL_PORT} — port was grabbed or device unplugged?")
                    print(f"Details: {e}")
                    break
                if not line:
                    continue
                if line.startswith("#"):
                    if line.startswith("# ERROR") or line.startswith("# WARNING"):
                        print(line)
                    if line.startswith("# FIRMWARE_VERSION="):
                        print(f"Detected {line}")
                    continue
                if line.startswith("lux"):
                    continue
                parts = line.split(",")
                if len(parts) != 6:
                    print(f"rejected (bad_field_count_{len(parts)}): {line}")
                    continue
                try:
                    vals = [float(x) for x in parts]
                except ValueError:
                    print(f"rejected (parse_error): {line}")
                    continue
                HEADER = ["timestamp", "lux", "ldr", "temp_c", "voltage_v", "current_ma", "power_mw", "label"]
                nan_cols = []
                for col_name, v in zip(HEADER[1:-1], vals):
                    if math.isnan(v):
                        nan_cols.append(col_name)
                if nan_cols:
                    print(f"rejected (nan_in:{','.join(nan_cols)}): {line}")
                    continue
                add_reading(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), None, *vals)
        except Exception as e:
            failure_count += 1
            if failure_count == 1:
                print(f"!!! Serial error on {SERIAL_PORT}: {e}")
            elif failure_count % 10 == 0:
                print(f"    Serial still failing ({failure_count}): {e}")
            time.sleep(3)
        finally:
            if ser is not None and ser.is_open:
                try:
                    ser.close()
                    print(f"Port {SERIAL_PORT} closed.")
                except Exception:
                    pass

def seed_history(days=180):
    random.seed(7)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for d in range(days, 0, -1):
        day = today - timedelta(days=d)
        weather = random.uniform(0.35, 1.0)
        health = 1 - AGING_PER_DAY * (days - d)
        bad_hour = random.randint(9, 14) if random.random() < 0.06 else -1
        for m in range(7 * 60, 18 * 60, 15):
            h = m / 60
            ts = (day + timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S")
            sun = math.sin(math.pi * (h - 7) / 11)
            lux = max(0.0, 70000 * sun * weather * random.uniform(0.95, 1.05))
            temp = 24 + 12 * sun * weather + random.uniform(-0.5, 0.5)
            f = 0.5 if int(h) == bad_hour else 1.0
            p = K_P * lux * (1 - 0.004 * (temp - 25)) * health * f * random.uniform(0.97, 1.03)
            v = 5.0 + 0.8 * min(1.0, lux / 20000)
            i = p / v
            label, conf, pr = predict(lux, temp, v, i, p)
            rows.append((STRING_ID, ts, None, lux, 2000, temp, v, i, p, label, conf, pr, 1, None))
    run(INSERT, many=rows)
    print(f"Seeded {len(rows)} simulated history rows")

def demo_live():
    state, n = "normal", 0
    while True:
        if n % 30 == 0:
            state = random.choice(["normal", "normal", "dirty", "partial_shading", "open_circuit"])
        n += 1
        lux = random.uniform(30000, 50000)
        temp = random.uniform(30, 40)
        factor = {"normal": 0.97, "dirty": 0.7, "partial_shading": 0.4, "open_circuit": 0.0}[state]
        base = K_P * lux * (1 - 0.004 * (temp - 25))
        v = 6.4 if state == "open_circuit" else random.uniform(4.8, 5.4)
        p = base * factor * random.uniform(0.97, 1.03)
        add_reading(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), None, lux, 2000, temp, v, p / v, p, 1, ground_truth=state if DEMO else None)
        time.sleep(5)

def daily_stats(days=60):
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = run("SELECT ts,power_mw,label,pr,simulated FROM readings WHERE string_id=? AND ts>=? ORDER BY ts",
               (STRING_ID, since))
    out = {}
    for r in rows:
        d = r["ts"][:10]
        t = datetime.fromisoformat(r["ts"])
        D = out.setdefault(d, {"label": d, "wh": 0.0, "peak_mw": 0.0, "prs": [], "faults": 0, "sim": False,
                               "_last": None, "_lab": None})
        if D["_last"] is not None:
            dt = min((t - D["_last"]).total_seconds(), 900)
            D["wh"] += r["power_mw"] * dt / 3600 / 1000
        D["_last"] = t
        D["peak_mw"] = max(D["peak_mw"], r["power_mw"])
        if r["label"] == "normal" and r["pr"] is not None:
            D["prs"].append(r["pr"])
        if r["label"] in FAULTS and D["_lab"] != r["label"]:
            D["faults"] += 1
        D["_lab"] = r["label"]
        D["sim"] = D["sim"] or bool(r["simulated"])
    res = []
    for d in sorted(out):
        D = out[d]
        res.append({"label": d, "wh": round(D["wh"], 3), "peak_mw": round(D["peak_mw"], 1),
                    "pr": round(sum(D["prs"]) / len(D["prs"]), 4) if D["prs"] else None,
                    "faults": D["faults"], "sim": D["sim"]})
    return res

def group(items, keyfn):
    g = {}
    for x in items:
        g.setdefault(keyfn(x), []).append(x)
    res = []
    for k in sorted(g):
        xs = g[k]
        prs = [x["pr"] for x in xs if x["pr"] is not None]
        best = max(xs, key=lambda x: x["wh"])
        worst = min(xs, key=lambda x: x["wh"])
        res.append({"label": k, "wh": round(sum(x["wh"] for x in xs), 2),
                    "pr": round(sum(prs) / len(prs), 4) if prs else None,
                    "faults": sum(x["faults"] for x in xs), "days": len(xs),
                    "best_day": best["label"], "worst_day": worst["label"],
                    "sim": any(x["sim"] for x in xs)})
    return res

app = FastAPI(title="Solar Monitor")

@app.on_event("startup")
def startup():
    ensure_schema()
    import_csv_if_exists()
    print("=== Solar Monitor startup ===")
    if SERIAL_PORT:
        print(f"  Serial:        {SERIAL_PORT}  (only reader — close logger.py and Arduino Serial Monitor before starting)")
    else:
        print(f"  Serial:        disabled (SERIAL_PORT not set)")
    if TS_CHANNEL:
        print(f"  ThingSpeak:    channel {TS_CHANNEL} (poll every {POLL_SEC}s, max age {MAX_TS_AGE_HOURS}h)")
    else:
        print(f"  ThingSpeak:    disabled (TS_CHANNEL not set)")
    print(f"  Cloud guard:   MAX_TS_AGE_HOURS={MAX_TS_AGE_HOURS}h")
    print(f"  Demo:          {'on (fake live data)' if DEMO else 'off'}")
    if DEMO_HISTORY:
        print(f"  History:       simulated (DEMO_HISTORY on)")
    else:
        print(f"  History:       live only")
    if MODEL is not None:
        print(f"  Model:         ML (model.joblib)")
    else:
        print(f"  Model:         rule-based (no model.joblib)")
    if _cal:
        print(f"  Calibration:   calib.json (K_P={K_P}, LOW_LUX={LOW_LUX})")
    else:
        print(f"  Calibration:   defaults (K_P={K_P}, LOW_LUX={LOW_LUX})")
    if SERIAL_PORT:
        print(f"  NOTE: Only one program can read the ESP32's USB port at a time.")
    print(f"==============================")
    if DEMO or DEMO_HISTORY:
        if run("SELECT COUNT(*) c FROM readings WHERE simulated=1", one=True)["c"] == 0:
            seed_history()
    if DEMO:
        threading.Thread(target=demo_live, daemon=True).start()
    if SERIAL_PORT:
        threading.Thread(target=serial_loop, daemon=True).start()
    if TS_CHANNEL:
        threading.Thread(target=poll_loop, daemon=True).start()

@app.get("/api/latest")
def latest():
    r = run("SELECT * FROM readings WHERE string_id=? ORDER BY ts DESC, id DESC LIMIT 1", (STRING_ID,), one=True)
    if not r:
        return {"reading": None}
    r = dict(r)
    today = datetime.now().strftime("%Y-%m-%d")
    t = next((d for d in daily_stats(1) if d["label"] == today), None)
    # Task 1: sim at top level - bool of simulated column
    return {"reading": r, "advice": ADVICE.get(r["label"], ""), "fault": r["label"] in FAULTS,
            "today_wh": t["wh"] if t else 0, "model": "ML model" if MODEL else "rule-based",
            "sim": bool(r.get("simulated", 0))}

@app.get("/api/readings")
def readings(limit: int = 120):
    # Task 1: include simulated in SELECT and each returned row
    rows = run("SELECT ts,power_mw,lux,voltage_v,current_ma,label,ground_truth,simulated FROM readings WHERE string_id=? "
               "ORDER BY ts DESC, id DESC LIMIT ?", (STRING_ID, limit))
    # dict(r) already includes simulated; also add sim alias for consistency
    result = []
    for rr in reversed(rows):
        d = dict(rr)
        d["sim"] = bool(d.get("simulated", 0))
        result.append(d)
    return result

@app.get("/api/daily")
def daily(days: int = 30):
    return daily_stats(days)[-days:]

@app.get("/api/weekly")
def weekly():
    res = group(daily_stats(120), lambda x: "%d-W%02d" % datetime.strptime(x["label"], "%Y-%m-%d").isocalendar()[:2])
    return res[-16:]

@app.get("/api/monthly")
def monthly():
    res = group(daily_stats(400), lambda x: x["label"][:7])
    prev = None
    for m in res:
        m["change_pct"] = round((m["pr"] / prev - 1) * 100, 2) if prev and m["pr"] else None
        prev = m["pr"] or prev
    return res

@app.get("/api/health")
def health():
    d = [x for x in daily_stats(400) if x["pr"] is not None]
    if len(d) < 8:
        return {"health_pct": None, "aging_pct_per_month": None, "days": len(d)}
    base = sum(x["pr"] for x in d[:7]) / 7
    recent = sum(x["pr"] for x in d[-7:]) / 7
    xs = [datetime.strptime(x["label"], "%Y-%m-%d").toordinal() for x in d]
    ys = [x["pr"] for x in d]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0
    return {"health_pct": round(recent / base * 100, 1), "aging_pct_per_month": round(slope * 30 / base * 100, 2),
            "days": len(d), "sim": any(x["sim"] for x in d)}

@app.get("/api/alerts")
def alerts():
    """
    Returns fault episodes grouped from consecutive fault readings.
    Episode grouping: consecutive same-label within 300s = one episode.
    Returns 4-5 keys per episode: label, start, end, advice, ground_truth? (optional)
    ground_truth is Stage D addition for evaluation:
      - Comes from DEMO=1 (true state), import_csv_if_exists (label from solar_data.csv), or logger.py labeled collection
      - Used for ML accuracy evaluation: compare predicted label vs ground_truth
      - Only included in episode if at least one reading in episode has non-null ground_truth
      - Live data (serial/ThingSpeak) has ground_truth=null -> field omitted for cleaner API
    Dashboard gracefully ignores ground_truth if not used (forward compat).
    """
    q = ",".join("'%s'" % f for f in FAULTS)
    rows = run(f"SELECT ts,label,confidence,ground_truth FROM readings WHERE string_id=? AND label IN ({q}) "
               "ORDER BY ts DESC LIMIT 3000", (STRING_ID,))
    eps = []
    for r in reversed(rows):
        t = datetime.fromisoformat(r["ts"])
        if eps and eps[-1]["label"] == r["label"] and (t - eps[-1]["_end"]).total_seconds() <= 300:
            eps[-1]["_end"] = t
            eps[-1]["end"] = r["ts"]
            if r["ground_truth"] is not None:
                eps[-1]["ground_truth"] = r["ground_truth"]
        else:
            ep = {"label": r["label"], "start": r["ts"], "end": r["ts"], "_end": t}
            if r["ground_truth"] is not None:
                ep["ground_truth"] = r["ground_truth"]
            eps.append(ep)
    for e in eps:
        e.pop("_end")
        e["advice"] = ADVICE[e["label"]]
    return eps[-30:][::-1]

app.mount("/", StaticFiles(directory=os.path.join(BASE, "static"), html=True), name="static")
