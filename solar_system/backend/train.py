"""
Train the fault classifier from logger.py data.

Put solar_data.csv in the same folder as this file, then:
    pip install pandas scikit-learn joblib matplotlib
    python train.py

Outputs (next to this file):
    model.joblib        -> main.py loads it automatically
    calib.json          -> K_P and LOW_LUX learned from your real 'normal' / 'low_light' data
    confusion_matrix.png-> use in your report
Restart uvicorn afterwards.
"""
import argparse
import json
import os

import joblib
import matplotlib
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import (ConfusionMatrixDisplay, accuracy_score,
                             classification_report, confusion_matrix, f1_score)
from sklearn.tree import DecisionTreeClassifier

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--csv", default=os.path.join(BASE, "solar_data.csv"))
args = ap.parse_args()

FEATURES = ["lux", "temp_c", "voltage_v", "current_ma", "power_mw", "pr"]  # same order as main.py

df = pd.read_csv(args.csv)
df["timestamp"] = pd.to_datetime(df["timestamp"])
df = df.sort_values("timestamp").reset_index(drop=True)
print("Rows per class:\n", df["label"].value_counts(), "\n")

# ---- calibration from real data ----
tf = 1 - 0.004 * (df["temp_c"] - 25)
norm = df[(df["label"] == "normal") & (df["lux"] > 0)]
k_p = float((norm["power_mw"] / (norm["lux"] * tf[norm.index])).median())
low_lux = 3000.0
if {"low_light", "normal"} <= set(df["label"]):
    low_lux = float((df[df.label == "low_light"]["lux"].quantile(0.9) + norm["lux"].quantile(0.1)) / 2)
print(f"Calibrated K_P = {k_p:.5f} mW/lux, LOW_LUX = {low_lux:.0f}\n")

expected = k_p * df["lux"] * tf
df["pr"] = (df["power_mw"] / expected).where(expected > 0, 0.0)

# ---- recording sessions (a new run = label change or >60 s gap) ----
new_run = (df["label"] != df["label"].shift()) | (df["timestamp"].diff().dt.total_seconds() > 60)
df["run"] = new_run.cumsum()

# ---- honest split: hold out a whole session, never random rows ----
train_idx, test_idx = [], []
for lab, g in df.groupby("label"):
    runs = sorted(g["run"].unique())
    if len(runs) >= 2:
        test_idx += list(g[g["run"] == runs[-1]].index)
        train_idx += list(g[g["run"] != runs[-1]].index)
    else:
        idx = list(g.sort_values("timestamp").index)
        cut = int(len(idx) * 0.7)
        train_idx += idx[:cut]
        test_idx += idx[cut:]
        print(f"WARNING: '{lab}' has only 1 session; using the last 30% in time as test. "
              f"Record a second session for a more trustworthy score.")
train, test = df.loc[train_idx], df.loc[test_idx]
print(f"\nTrain rows: {len(train)}  Test rows: {len(test)}\n")

models = {
    "Decision Tree": DecisionTreeClassifier(max_depth=6, class_weight="balanced", random_state=0),
    "Random Forest": RandomForestClassifier(n_estimators=200, class_weight="balanced", random_state=0),
    "Gradient Boosting": GradientBoostingClassifier(random_state=0),
}
scores = {}
for name, m in models.items():
    m.fit(train[FEATURES], train["label"])
    pred = m.predict(test[FEATURES])
    scores[name] = (accuracy_score(test["label"], pred), f1_score(test["label"], pred, average="macro"))
    print(f"{name:18s} accuracy={scores[name][0]:.3f}  macro-F1={scores[name][1]:.3f}")

best = max(scores, key=lambda k: scores[k][1])
print(f"\nBest model: {best}\n")
m = models[best]
pred = m.predict(test[FEATURES])
print(classification_report(test["label"], pred, zero_division=0))

labels = sorted(df["label"].unique())
ConfusionMatrixDisplay(confusion_matrix(test["label"], pred, labels=labels), display_labels=labels).plot(
    xticks_rotation=30, cmap="Blues")
plt.title(f"{best} - held-out session")
plt.tight_layout()
plt.savefig(os.path.join(BASE, "confusion_matrix.png"), dpi=150)

if hasattr(m, "feature_importances_"):
    print("Feature importance:")
    for f, v in sorted(zip(FEATURES, m.feature_importances_), key=lambda x: -x[1]):
        print(f"  {f:12s} {v:.3f}")

# ---- refit on all data and save ----
m.fit(df[FEATURES], df["label"])
joblib.dump(m, os.path.join(BASE, "model.joblib"))
with open(os.path.join(BASE, "calib.json"), "w") as f:
    json.dump({"K_P": k_p, "LOW_LUX": low_lux}, f)
print("\nSaved model.joblib, calib.json, confusion_matrix.png. Restart uvicorn.")