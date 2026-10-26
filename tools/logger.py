"""
Solar data logger (Phase 2)

Install once:   pip install pyserial
Run example:    python logger.py --port COM3 --label normal --rows 100

Close Arduino Serial Monitor first (only one program can use the COM port).
Rows are APPENDED to solar_data.csv, so you can run it many times with different labels.
"""
import argparse
import csv
import math
import os
from datetime import datetime

import serial

LABELS = ["normal", "low_light", "partial_shading", "dirty", "open_circuit"]
HEADER = ["timestamp", "lux", "ldr", "temp_c", "voltage_v", "current_ma", "power_mw", "label"]

ap = argparse.ArgumentParser()
ap.add_argument("--port", default="COM3")
ap.add_argument("--label", required=True, choices=LABELS)
ap.add_argument("--rows", type=int, default=100)
ap.add_argument("--file", default="solar_data.csv")
args = ap.parse_args()

new_file = not os.path.exists(args.file)
ser = serial.Serial(args.port, 115200, timeout=3)
print(f"Logging {args.rows} rows as '{args.label}' to {args.file} ... Ctrl+C to stop early")

count = 0
with open(args.file, "a", newline="") as f:
    w = csv.writer(f)
    if new_file:
        w.writerow(HEADER)
    try:
        while count < args.rows:
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
                print("skipped (NaN):", line)
                continue
            w.writerow([datetime.now().isoformat(timespec="seconds")] + parts + [args.label])
            f.flush()
            count += 1
            print(f"{count}/{args.rows}  {line}")
    except KeyboardInterrupt:
        pass
ser.close()
print(f"Done. {count} rows saved.")