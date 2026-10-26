"""
Solar Monitor backend (FastAPI + SQLite), USB serial version.
Normally started with run_dashboard.bat from the project folder.

Manual start (Command Prompt, in this folder):
    set SERIAL_PORT=COM3
    set DEMO_HISTORY=1          (simulated history for weekly/monthly pages, clearly labelled)
    python -m uvicorn main:app
Fake live data for testing without hardware:  set DEMO=1

Uses model.joblib + calib.json (made by train.py) when present, otherwise simple rules.
"""
import math
import os
import random
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import requests
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "solar.db")
TS_CHANNEL = 3551353
TS_READ_KEY = "6TD0ANB8JNI4GOXA"
STRING_ID = os.getenv("STRING_ID", "string1")
POLL_SEC = int(os.getenv("POLL_SEC", "60"))
DEMO = os.getenv("DEMO", "0") == "1"                       # simulated history + fake live data
DEMO_HISTORY = os.getenv("DEMO_HISTORY", "0") == "1"       # simulated history only (use with real live data)
SERIAL_PORT = os.getenv("SERIAL_PORT", "")                 # e.g. COM3: read the ESP32 over USB
import json
_cal_file = os.path.join(BASE, "calib.json")   # written by train.py
_cal = json.load(open(_cal_file)) if os.path.exists(_cal_file) else {}
K_P = float(os.getenv("K_P", _cal.get("K_P", 0.005)))             # mW per lux, healthy panel
LOW_LUX = float(os.getenv("LOW_LUX", _cal.get("LOW_LUX", 3000)))  # below this = low_light
AGING_PER_DAY = 0.0004                          # simulated history only (accelerated demo)

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


# ---------- fault detection ----------
MODEL = None
try:
    import joblib
    if os.path.exists(os.path.join(BASE, "model.joblib")):
        MODEL = joblib.load(os.path.join(BASE, "model.joblib"))
        print("Loaded trained model.joblib")
except Exception as e:
    print("No ML model loaded:", e)


def predict(lux, temp, v, i, p):
    """Returns (label, confidence, performance_ratio). Features for ML: lux,temp_c,voltage_v,current_ma,power_mw,pr"""
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
          "label,confidence,pr,simulated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)")


def add_reading(ts, entry, lux, ldr, temp, v, i, p, sim=0):
    label, conf, pr = predict(lux, temp, v, i, p)
    run(INSERT, (STRING_ID, ts, entry, lux, ldr, temp, v, i, p, label, conf, pr, sim))


# ---------- data sources ----------
def poll_loop():
    url = f"https://api.thingspeak.com/channels/{TS_CHANNEL}/feeds.json"
    params = {"results": 200}
    if TS_READ_KEY:
        params["api_key"] = TS_READ_KEY
    while True:
        try:
            feeds = requests.get(url, params=params, timeout=15).json().get("feeds", [])
            for f in feeds:
                try:
                    vals = [float(f[f"field{k}"]) for k in range(1, 7)]
                except (TypeError, ValueError, KeyError):
                    continue
                ts = datetime.fromisoformat(f["created_at"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
                add_reading(ts.strftime("%Y-%m-%d %H:%M:%S"), f"ts-{f['entry_id']}", *vals)
        except Exception as e:
            print("poll error:", e)
        time.sleep(POLL_SEC)


def serial_loop():
    """Read CSV lines from the ESP32 over USB. Close Arduino Serial Monitor / logger.py first."""
    import serial
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, 115200, timeout=5)
            print("Serial connected:", SERIAL_PORT)
            while True:
                line = ser.readline().decode(errors="ignore").strip()
                if not line or line.startswith("#") or line.startswith("lux"):
                    continue
                parts = line.split(",")
                if len(parts) != 6:
                    continue
                try:
                    vals = [float(x) for x in parts]
                except ValueError:
                    continue
                if any(math.isnan(v) for v in vals):
                    continue
                add_reading(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), None, *vals)
        except Exception as e:
            print("serial error:", e)
            time.sleep(3)


def seed_history(days=180):
    """SIMULATED history so the weekly/monthly pages have data. Flagged simulated=1."""
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
            rows.append((STRING_ID, ts, None, lux, 2000, temp, v, i, p, label, conf, pr, 1))
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
        add_reading(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), None, lux, 2000, temp, v, p / v, p, 1)
        time.sleep(5)


# ---------- analytics ----------
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


# ---------- API ----------
app = FastAPI(title="Solar Monitor")


@app.on_event("startup")
def startup():
    run("""CREATE TABLE IF NOT EXISTS readings(
        id INTEGER PRIMARY KEY AUTOINCREMENT, string_id TEXT, ts TEXT, entry TEXT UNIQUE,
        lux REAL, ldr REAL, temp_c REAL, voltage_v REAL, current_ma REAL, power_mw REAL,
        label TEXT, confidence REAL, pr REAL, simulated INTEGER DEFAULT 0)""")
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
    return {"reading": r, "advice": ADVICE.get(r["label"], ""), "fault": r["label"] in FAULTS,
            "today_wh": t["wh"] if t else 0, "model": "ML model" if MODEL else "rule-based"}


@app.get("/api/readings")
def readings(limit: int = 120):
    rows = run("SELECT ts,power_mw,lux,voltage_v,current_ma,label FROM readings WHERE string_id=? "
               "ORDER BY ts DESC, id DESC LIMIT ?", (STRING_ID, limit))
    return [dict(r) for r in reversed(rows)]


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
    q = ",".join("'%s'" % f for f in FAULTS)
    rows = run(f"SELECT ts,label,confidence FROM readings WHERE string_id=? AND label IN ({q}) "
               "ORDER BY ts DESC LIMIT 3000", (STRING_ID,))
    eps = []
    for r in reversed(rows):
        t = datetime.fromisoformat(r["ts"])
        if eps and eps[-1]["label"] == r["label"] and (t - eps[-1]["_end"]).total_seconds() <= 300:
            eps[-1]["_end"] = t
            eps[-1]["end"] = r["ts"]
        else:
            eps.append({"label": r["label"], "start": r["ts"], "end": r["ts"], "_end": t})
    for e in eps:
        e.pop("_end")
        e["advice"] = ADVICE[e["label"]]
    return eps[-30:][::-1]


app.mount("/", StaticFiles(directory=os.path.join(BASE, "static"), html=True), name="static")