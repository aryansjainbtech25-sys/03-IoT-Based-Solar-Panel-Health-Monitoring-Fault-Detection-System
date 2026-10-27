"""
Solar data logger (Phase 2) - ESP32 -> solar_data.csv gateway

Original: Required --label, appended timestamped rows to solar_data.csv, ignored # and header lines.

B.2 - Rejected row traceability:
    - Creates rejected_{basename} in same dir as --file (e.g. rejected_solar_data.csv)
    - Header: timestamp, raw_line, reason, label
    - Reasons: bad_field_count_{n}, parse_error, nan_in:{col}, e.g. nan_in:temp_c or nan_in:lux,temp_c
    - Purpose: If 0 rows saved or 63/100, open rejected file to see why (boot spam "00", "mw", "try 0x40...", concatenated lines)
    - Clean CSV (solar_data.csv) is unchanged, only good 6-field rows.

B.3 - Soft-validate # FIRMWARE_VERSION:
    - Firmware sends "# FIRMWARE_VERSION=1" at boot and every ~30s (in loop()).
    - Logger detects line starting with "# FIRMWARE_VERSION=", prints "Detected # FIRMWARE_VERSION=1"
    - After first data row, if no version seen, prints WARNING: No # FIRMWARE_VERSION line seen...
    - If version != "1", warns about unexpected version (schema may have changed).
    - Final line: Done. X rows saved. Firmware: 1 or UNKNOWN

B.4 - Print # ERROR and # WARNING:
    - Lines starting with "# ERROR" or "# WARNING" from firmware (e.g. DS18B20 not found, voltage >8.5V)
    are printed to console immediately, not silently ignored.
    - Example: "# ERROR: DS18B20 not found" -> visible at start of run.

B.5 - Graceful port open/read failures:
    - Wraps serial.Serial() open and readline() in try/except SerialException.
    - On failure (port busy, non-existent /dev/ttyUSB99, multiple access), prints friendly message:
    "Could not open port X — is another program using it? (【entity-Arduino¦canonical_name=Arduino】 Serial Monitor / another logger / miniterm)"
        + details + tip "sudo fuser -k X" and exits cleanly (no traceback).

B.6 - Guaranteed ser.close():
    - Main loop in try/finally, ser.close() always runs (normal exit, Ctrl+C, unplug, SerialException).
    - Prints "Port X closed." Ensures next run doesn't get "device busy".

CSV Contract (from firmware):
    - 6 fields: lux,ldr,temp_c,voltage_v,current_ma,power_mw
    - Precision: %.1f,%d,%.2f,%.3f,%.2f,%.2f, NAN as nan/-nan
    - # lines are status, ignored for CSV (except B.3/B.4 logging)
    - No timestamp in firmware, PC adds timestamp

Usage:
  python3 tools/logger.py --port /dev/ttyUSB0 --label normal --rows 100
  python3 tools/logger.py --port COM3 --label low_light --rows 200 --file solar_data.csv
  Default port COM3 - change to /dev/ttyUSB0 on Linux/Mac.

.gitignore note:
  Add to .gitignore:
    rejected_solar_data.csv
    rejected_*.csv
  Raw data debug artifacts should not be committed.

Phase 2 gateway: ESP32 does NOT upload to cloud, PC is gateway.
"""
import argparse
import csv
import math
import os
import sys
from datetime import datetime
import serial

LABELS = ["normal", "low_light", "partial_shading", "dirty", "open_circuit"]
HEADER = ["timestamp", "lux", "ldr", "temp_c", "voltage_v", "current_ma", "power_mw", "label"]
REJECTED_HEADER = ["timestamp", "raw_line", "reason", "label"]

ap = argparse.ArgumentParser()
ap.add_argument("--port", default="COM3", help="Serial port, e.g. COM3 on Windows or /dev/ttyUSB0 on Linux/Mac")
ap.add_argument("--label", required=True, choices=LABELS)
ap.add_argument("--rows", type=int, default=100)
ap.add_argument("--file", default="solar_data.csv")
args = ap.parse_args()

file_dir = os.path.dirname(args.file) or "."
file_base = os.path.basename(args.file)
rejected_file_path = os.path.join(file_dir, f"rejected_{file_base}")

new_file = not os.path.exists(args.file)
new_rejected_file = not os.path.exists(rejected_file_path)

saw_firmware_version = False
firmware_version_str = None
version_warning_printed = False
EXPECTED_FW_PREFIX = "# FIRMWARE_VERSION="

ser = None
try:
    ser = serial.Serial(args.port, 115200, timeout=3)
except serial.SerialException as e:
    print(f"Could not open port {args.port} — is another program using it? (Arduino Serial Monitor / another logger / miniterm)")
    print(f"Details: {e}")
    print(f"Tip: Close Arduino IDE Serial Monitor, close other terminals, then run: sudo fuser -k {args.port}")
    sys.exit(1)

print(f"Logging {args.rows} rows as '{args.label}' to {args.file}")
print(f"Rejected -> {rejected_file_path}")

count = 0
try:
    with open(args.file, "a", newline="") as f, open(rejected_file_path, "a", newline="") as rf:
        w = csv.writer(f)
        rw = csv.writer(rf)
        if new_file:
            w.writerow(HEADER)
        if new_rejected_file:
            rw.writerow(REJECTED_HEADER)
        try:
            while count < args.rows:
                try:
                    line = ser.readline().decode(errors="ignore").strip()
                except serial.SerialException as e:
                    print(f"Serial read failed on {args.port} — port was grabbed or device unplugged?")
                    print(f"Details: {e}")
                    print(f"Tip: Check USB cable, close other program using {args.port}")
                    break

                if not line:
                    continue

                if line.startswith("#"):
                    if line.startswith("# ERROR") or line.startswith("# WARNING"):
                        print(line)
                    if line.startswith(EXPECTED_FW_PREFIX):
                        saw_firmware_version = True
                        try:
                            firmware_version_str = line.split("=", 1)[1].strip()
                        except IndexError:
                            firmware_version_str = ""
                        print(f"Detected {line}")
                        if firmware_version_str != "1" and firmware_version_str != "":
                            print(f"# WARNING: Unexpected firmware version '{firmware_version_str}' (expected 1) - check CSV schema!")
                    continue

                if line.startswith("lux"):
                    continue

                parts = line.split(",")
                if len(parts) != 6:
                    reason = f"bad_field_count_{len(parts)}"
                    rw.writerow([datetime.now().isoformat(timespec="seconds"), line, reason, args.label])
                    rf.flush()
                    print(f"rejected ({reason}): {line}")
                    continue
                try:
                    vals = [float(x) for x in parts]
                except ValueError:
                    reason = "parse_error"
                    rw.writerow([datetime.now().isoformat(timespec="seconds"), line, reason, args.label])
                    rf.flush()
                    print(f"rejected ({reason}): {line}")
                    continue

                # --- Task 4 spec: reason with column names ---
                nan_cols = []
                for idx, v in enumerate(vals):
                    if math.isnan(v):
                        # HEADER[1]=lux, HEADER[2]=ldr, HEADER[3]=temp_c etc. vals idx 0->HEADER[1]
                        col_name = HEADER[idx + 1]
                        nan_cols.append(col_name)
                if nan_cols:
                    reason = f"nan_in:{','.join(nan_cols)}"
                    rw.writerow([datetime.now().isoformat(timespec="seconds"), line, reason, args.label])
                    rf.flush()
                    print(f"rejected ({reason}): {line}")
                    continue

                w.writerow([datetime.now().isoformat(timespec="seconds")] + parts + [args.label])
                f.flush()
                count += 1
                print(f"{count}/{args.rows}  {line}")

                if count == 1 and not saw_firmware_version and not version_warning_printed:
                    print("# WARNING: No # FIRMWARE_VERSION line seen before first data row - firmware may have changed column order!")
                    print("# WARNING: Verify firmware sends '# FIRMWARE_VERSION=1' and CSV header matches: lux,ldr,temp_c,voltage_v,current_ma,power_mw")
                    version_warning_printed = True

        except KeyboardInterrupt:
            print("\nInterrupted by user (Ctrl+C)")

except Exception as e:
    print(f"Unexpected error: {e}")

finally:
    if ser is not None and ser.is_open:
        try:
            ser.close()
            print(f"Port {args.port} closed.")
        except Exception:
            pass

    if not saw_firmware_version and not version_warning_printed and count > 0:
        print("# WARNING: Logging ended without ever seeing # FIRMWARE_VERSION line - check firmware / wiring / baud!")

    print(f"Done. {count} rows saved. Firmware: {firmware_version_str if saw_firmware_version else 'UNKNOWN'}")
