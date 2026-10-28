"""
CSV importer for solar_data.csv -> solar.db
Option A: Standalone script to import real historical data for monthly monitoring (Issue 5)
Usage: python3 tools/import_csv.py --file solar_data.csv
Idempotent: uses entry=csv-{ts}-{label} UNIQUE, so running twice doesn't duplicate
Guard: none for standalone (always tries), but INSERT OR IGNORE prevents dup
Does NOT respect MAX_TS_AGE_HOURS (historical data intentionally old)
"""
import csv, os, sqlite3, argparse
from datetime import datetime

parser = argparse.ArgumentParser()
parser.add_argument("--file", default="solar_data.csv", help="Path to solar_data.csv")
parser.add_argument("--db", default="solar_system/backend/solar.db", help="Path to solar.db")
args = parser.parse_args()

if not os.path.exists(args.file):
    print(f"File not found: {args.file}")
    exit(1)

conn = sqlite3.connect(args.db)
conn.execute("""CREATE TABLE IF NOT EXISTS readings(
        id INTEGER PRIMARY KEY AUTOINCREMENT, string_id TEXT, ts TEXT, entry TEXT UNIQUE,
        lux REAL, ldr REAL, temp_c REAL, voltage_v REAL, current_ma REAL, power_mw REAL,
        label TEXT, confidence REAL, pr REAL, simulated INTEGER DEFAULT 0,
        ground_truth TEXT)""")

K_P = 0.005
def predict(lux,temp,v,i,p):
    expected = K_P * lux * (1 - 0.004 * (temp - 25)) if lux>0 else 0
    pr = p/expected if expected>0 else 0.0
    if lux < 3000: return "low_light",0.8,pr
    if i<0.5 and v>2.0: return "open_circuit",0.9,pr
    if pr>=0.85: return "normal",0.8,pr
    if pr>=0.55: return "dirty",0.6,pr
    return "partial_shading",0.6,pr

imported=0
skipped=0
with open(args.file, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        try:
            ts = row.get("timestamp") or row.get("ts")
            lux = float(row["lux"])
            ldr = float(row.get("ldr",2000))
            temp = float(row["temp_c"])
            v = float(row["voltage_v"])
            i = float(row["current_ma"])
            p = float(row["power_mw"])
            label_gt = row.get("label","normal")  # ground_truth = human label from logger.py --label
            label_pred, conf, pr = predict(lux,temp,v,i,p)  # label = predict() output (model/rules)
            entry_id = f"csv-{ts}-{label_gt}"  # UNIQUE for idempotence
            cur = conn.execute("INSERT OR IGNORE INTO readings(string_id,ts,entry,lux,ldr,temp_c,voltage_v,current_ma,power_mw,label,confidence,pr,simulated,ground_truth) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("string1", ts, entry_id, lux, ldr, temp, v, i, p, label_pred, conf, pr, 0, label_gt))
            if cur.rowcount == 0:
                skipped+=1
            else:
                imported+=1
        except Exception as e:
            continue
conn.commit()
total = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
print(f"Imported {imported} new rows, skipped {skipped} duplicates from {args.file} into {args.db} (total now {total})")
print(f"Semantics: label=predict() output, ground_truth=CSV label (human-labeled), simulated=0 (real data for monthly E)")
conn.close()
