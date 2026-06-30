"""
tamper_engine.py — Core tamper detection logic for DroneGuard
Calibrated against NIST CFREDS clean DJI Phantom 3 dataset (FLY001-FLY010).
https://cfreds-archive.nist.gov/drone-images.html

DJI CSV format quirks (NOT signs of tampering):
  - messageid diffs of 0/1/2/3: multiple packet types interleaved in merged CSV
  - Large messageid jumps alone: session-block header records
  - Duplicate offsetTime: concurrent sensor packets at same millisecond
  - satnum==0 on most rows: those are IMU rows (GPS packet type not present)
  - IMU z-scores up to ~25: normal Phantom 3 prop vibration at flight speed
  - Battery quantised at 0.04V steps: single-step rises are converter artefacts
  - GPS frame-to-frame apparent speed: coordinate quantisation, not real movement
  - GPS altitude vs baroAlt when altitude=0: GPS not locked yet (baroAlt=MSL)
  - Sub-ms backward delta (-1.67ms): DAT→CSV converter float precision artefact
  - Motor RPM near zero: ground/spool-up, imbalance calculation meaningless
  - Voltage jumps between sessions: multi-battery mapping missions (normal)
"""

import os
import math
import numpy as np
import pandas as pd
from math import radians, sin, cos, sqrt, atan2
from datetime import datetime


# ════════════════════════════════════════════════════════════════════════════
#  HAVERSINE
# ════════════════════════════════════════════════════════════════════════════
def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlam = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlam / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


# ════════════════════════════════════════════════════════════════════════════
#  LOAD & CLEAN
# ════════════════════════════════════════════════════════════════════════════
def load_data(filepath):
    df = pd.read_csv(filepath, low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    numeric_cols = [
        "messageid", "offsetTime", "time(millisecond)",
        "latitude", "longitude", "satnum", "gpsHealth",
        "altitude", "baroAlt", "height",
        "accelX", "accelY", "accelZ", "accel",
        "gyroX", "gyroY", "gyroZ", "gyro",
        "velN", "velE", "velD", "vel", "velH",
        "roll", "pitch", "yaw", "yaw360",
        "totalVolts", "volt1", "volt2", "volt3", "volt4", "volt5", "volt6",
        "current", "Watts", "batteryTemp(C)",
        "remainingCapacity", "percentageCapacity",
        "rFront", "lFront", "lBack", "rBack",
        "imuTemp", "thrustAngle",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ════════════════════════════════════════════════════════════════════════════
#  TIMESTAMP CHECK
# ════════════════════════════════════════════════════════════════════════════
def check_timestamps(df):
    """
    Only flags:
    1. Backward timestamps larger than 0.1s (sub-ms deltas are CSV conversion artefacts)
    2. Time gaps > 30s with no corresponding sensor gap
    3. messageid jump >1000 with NO matching time gap (ID jumped but time did not)
    """
    anomalies = []
    ts_df = df[df["offsetTime"].notna()].copy()
    if ts_df.empty:
        return anomalies

    deltas = ts_df["offsetTime"].diff()

    # 1. Real backward timestamps — ignore sub-ms (< 0.1s) which are converter artefacts
    for idx in ts_df[deltas < -0.1].index:
        anomalies.append(_anomaly(
            row=idx, df=ts_df,
            atype="Timestamp — Backward",
            severity="CRITICAL",
            detail=f"Time decreased by {abs(deltas[idx]):.4f}s — rows may have been reordered"
        ))

    # 2. Very large gaps (>30s)
    for idx in ts_df[deltas > 30.0].index:
        anomalies.append(_anomaly(
            row=idx, df=ts_df,
            atype="Timestamp — Large Gap",
            severity="HIGH",
            detail=(
                f"Gap of {deltas[idx]:.1f}s between consecutive rows "
                f"(threshold: 30s) — rows may have been deleted"
            )
        ))

    # 3. MessageID suspicious: huge jump + tiny time delta (can't be session header)
    if "messageid" in df.columns:
        mid = df["messageid"].dropna()
        mid_diff = mid.diff().dropna()
        for idx, val in mid_diff.items():
            if val > 1000:
                try:
                    loc = mid.index.get_loc(idx)
                    if loc > 0:
                        prev_idx = mid.index[loc - 1]
                        tval  = float(df.at[idx,      "offsetTime"]) if "offsetTime" in df.columns else None
                        tprev = float(df.at[prev_idx, "offsetTime"]) if "offsetTime" in df.columns else None
                        if tval is not None and tprev is not None:
                            time_gap = tval - tprev
                            if 0 < time_gap < 0.5:
                                anomalies.append(_anomaly(
                                    row=idx, df=df,
                                    atype="MessageID — Suspicious Jump",
                                    severity="MEDIUM",
                                    detail=(
                                        f"MessageID jumped {int(val)} IDs in only {time_gap:.3f}s. "
                                        f"Session-block headers only appear at reconnect events (>1s gap)."
                                    )
                                ))
                except Exception:
                    pass

    return anomalies


# ════════════════════════════════════════════════════════════════════════════
#  GPS CONSISTENCY CHECK
# ════════════════════════════════════════════════════════════════════════════
def check_gps(df):
    """
    Calibrated GPS checks:
    - Speed computed over ≥1s windows (avoids GPS quantisation spikes)
    - Altitude vs baro only compared when both >1.0m (excludes pre-lock zeros)
    - Baro/GPS mismatch only flagged when diff >> local median (geoid-aware)
    - Satellite loss only on rows with actual GPS coordinates
    """
    anomalies = []

    MAX_SPEED_MS     = 55.6   # 200 km/h — Phantom 3 max spec
    MIN_SPEED_WIN_S  = 1.0    # Minimum time window for speed measurement
    MAX_BARO_DIFF_M  = 50.0   # Absolute cap; relative check uses median baseline

    gps = df[
        df["latitude"].notna() &
        df["longitude"].notna() &
        df["offsetTime"].notna() &
        (df["latitude"].abs()  > 0.001) &
        (df["longitude"].abs() > 0.001)
    ].copy()

    if len(gps) < 2:
        return anomalies

    lats  = gps["latitude"].values
    lons  = gps["longitude"].values
    times = gps["offsetTime"].values
    idxs  = gps.index.tolist()

    # 1. Speed — measured over ≥1s windows to avoid quantisation noise
    i = 0
    while i < len(gps):
        j = i + 1
        while j < len(gps) and (times[j] - times[i]) < MIN_SPEED_WIN_S:
            j += 1
        if j >= len(gps):
            break
        dt = times[j] - times[i]
        if dt > 0:
            dist_m   = haversine_m(lats[i], lons[i], lats[j], lons[j])
            speed_ms = dist_m / dt
            if speed_ms > MAX_SPEED_MS:
                anomalies.append(_anomaly(
                    row=idxs[j], df=gps,
                    atype="GPS — Impossible Speed",
                    severity="CRITICAL",
                    detail=(
                        f"Speed {speed_ms*3.6:.1f} km/h over {dt:.2f}s window "
                        f"exceeds Phantom 3 max (200 km/h). Distance: {dist_m:.1f}m"
                    )
                ))
        i = j

    # 2. Altitude jump — only between rows both with altitude > 1m
    alt_col = "altitude"
    if "altitude" not in df.columns or df["altitude"].notna().sum() == 0:
        alt_col = "baroAlt"
    if alt_col in df.columns:
        alt_valid = df[df[alt_col].notna() & (df[alt_col] > 1.0)][alt_col]
        if len(alt_valid) > 1:
            alt_diff = alt_valid.diff().abs()
            # Cross-check with time delta
            for idx in alt_diff[alt_diff > 100.0].index:
                loc = alt_valid.index.get_loc(idx)
                if loc > 0:
                    prev_idx = alt_valid.index[loc - 1]
                    try:
                        dt_alt = abs(float(df.at[idx, "offsetTime"]) -
                                     float(df.at[prev_idx, "offsetTime"]))
                        if dt_alt < 1.0:
                            anomalies.append(_anomaly(
                                row=idx, df=df,
                                atype="GPS — Altitude Jump",
                                severity="HIGH",
                                detail=(
                                    f"Altitude changed {alt_diff[idx]:.1f}m in {dt_alt:.3f}s "
                                    f"— exceeds physical climb/descent rate"
                                )
                            ))
                    except Exception:
                        pass

    # 3. GPS vs barometric cross-check — exclude zero-altitude rows (pre-lock)
    if "altitude" in df.columns and "baroAlt" in df.columns:
        both = df[
            df["altitude"].notna() & df["baroAlt"].notna() &
            (df["altitude"] > 1.0) & (df["baroAlt"] > 1.0)
        ].copy()
        if len(both) > 10:
            diff = (both["altitude"] - both["baroAlt"]).abs()
            baseline = diff.median()
            # Use relative threshold: only flag if >> the local geoid offset
            threshold = max(MAX_BARO_DIFF_M, baseline * 5 + 10)
            flagged = diff[diff > threshold]
            for idx in flagged.index:
                anomalies.append(_anomaly(
                    row=idx, df=df,
                    atype="GPS — Baro/GPS Mismatch",
                    severity="MEDIUM",
                    detail=(
                        f"GPS/baro altitude differ by {diff[idx]:.1f}m "
                        f"(local baseline: {baseline:.1f}m, threshold: {threshold:.1f}m)"
                    )
                ))

    # 4. Satellite loss — only on rows that have actual GPS coordinates
    if "satnum" in df.columns and len(gps) > 0:
        gps_with_sat = gps[gps["satnum"].notna()]
        no_sat = gps_with_sat[gps_with_sat["satnum"] == 0]
        for idx in no_sat.index:
            anomalies.append(_anomaly(
                row=idx, df=df,
                atype="GPS — Satellite Loss With Active Position",
                severity="MEDIUM",
                detail="GPS coordinates present but satnum=0 — position may be invalid or spoofed"
            ))

    return anomalies


# ════════════════════════════════════════════════════════════════════════════
#  STATISTICAL CHECKS
# ════════════════════════════════════════════════════════════════════════════
def check_statistics(df):
    """
    Calibrated statistical anomaly detection:
    - IMU Z-score threshold = 50 (Phantom 3 vibration reaches z~25 in clean data)
    - Battery: only sustained rolling-average rises >0.8V (excludes quantisation,
      load transients, and multi-battery swaps which are operational events)
    - Motor imbalance: only during flight (motor_mean > 500 RPM)
    """
    anomalies = []

    # 1. IMU spike — threshold z>50 (physically impossible: would destroy aircraft)
    imu_cols = [c for c in ["accelX", "accelY", "accelZ", "accel",
                             "gyroX", "gyroY", "gyroZ", "gyro"] if c in df.columns]
    for col in imu_cols:
        series = df[col].dropna()
        if len(series) < 50:
            continue
        mu, sigma = series.mean(), series.std()
        if sigma < 1e-9:
            continue
        z = (series - mu).abs() / sigma
        flagged = z[z > 50]
        if len(flagged) > 0:
            idxlist = flagged.index.tolist()
            for k, idx in enumerate(idxlist):
                if k == 0 or idx - idxlist[k-1] > 5:
                    anomalies.append(_anomaly(
                        row=idx, df=df,
                        atype=f"Statistical — IMU Spike ({col})",
                        severity="MEDIUM",
                        detail=(
                            f"{col}={df.at[idx, col]:.4f} is z={z[idx]:.0f} σ from mean "
                            f"({mu:.4f}±{sigma:.4f}) — physically impossible value"
                        )
                    ))

    # 2. Battery voltage — sustained rises only (>0.8V in rolling mean)
    #    Multi-battery swaps in long missions produce >1V rises but are operational.
    #    We flag these as MEDIUM (informational) not HIGH.
    if "totalVolts" in df.columns:
        v = df["totalVolts"].dropna()
        if len(v) > 100:
            roll = v.rolling(window=30, min_periods=15).mean()
            roll_diff = roll.diff()
            in_block, block_start, block_sum = False, None, 0.0
            for idx in roll_diff.index:
                val = roll_diff[idx]
                if pd.isna(val):
                    continue
                if val > 0.025:
                    if not in_block:
                        in_block, block_start, block_sum = True, idx, 0.0
                    block_sum += val
                else:
                    if in_block and block_sum > 0.8:
                        anomalies.append(_anomaly(
                            row=block_start, df=df,
                            atype="Statistical — Battery Voltage Sustained Rise",
                            severity="MEDIUM",
                            detail=(
                                f"Rolling battery voltage rose ~{block_sum:.2f}V over a sustained period. "
                                f"May indicate a battery swap (operational) or data splice (tamper). "
                                f"Review in context of flight state."
                            )
                        ))
                    in_block, block_sum = False, 0.0

    # 3. Velocity cross-check
    if all(c in df.columns for c in ["velN", "velE", "velH"]):
        both = df[df["velN"].notna() & df["velE"].notna() & df["velH"].notna()].copy()
        if len(both) > 10:
            computed = np.sqrt(both["velN"] ** 2 + both["velE"] ** 2)
            diff = (both["velH"] - computed).abs()
            big = diff[diff > 10.0]
            idxlist = big.index.tolist()
            for k, idx in enumerate(idxlist):
                if k == 0 or idx - idxlist[k-1] > 20:
                    anomalies.append(_anomaly(
                        row=idx, df=df,
                        atype="Statistical — Velocity Inconsistency",
                        severity="MEDIUM",
                        detail=(
                            f"velH={both.at[idx,'velH']:.2f} ≠ "
                            f"√(velN²+velE²)={computed[idx]:.2f} "
                            f"(diff {diff[idx]:.2f} m/s)"
                        )
                    ))

    # 4. Motor RPM imbalance — only during flight (mean RPM > 500)
    motor_cols = [c for c in ["rFront", "lFront", "lBack", "rBack"] if c in df.columns]
    if len(motor_cols) == 4:
        motors = df[motor_cols].dropna()
        if len(motors) > 0:
            row_mean = motors.mean(axis=1)
            row_max  = motors.max(axis=1)
            # Only check when actually flying
            flying_mask = row_mean > 500
            flying = motors[flying_mask]
            if len(flying) > 0:
                f_mean = row_mean[flying_mask]
                f_max  = row_max[flying_mask]
                imbal  = (f_max - f_mean) / (f_mean.replace(0, np.nan))
                flagged = imbal[imbal > 0.50].dropna()
                idxlist = flagged.index.tolist()
                for k, idx in enumerate(idxlist):
                    if k == 0 or idx - idxlist[k-1] > 10:
                        anomalies.append(_anomaly(
                            row=idx, df=df,
                            atype="Statistical — Motor RPM Imbalance",
                            severity="MEDIUM",
                            detail=(
                                f"Motor RPM spread {imbal[idx]*100:.0f}% during flight "
                                f"(threshold: 50%) — one motor significantly out of spec"
                            )
                        ))

    return anomalies


# ════════════════════════════════════════════════════════════════════════════
#  FLIGHT STATISTICS
# ════════════════════════════════════════════════════════════════════════════
def compute_flight_stats(df):
    stats = {}
    ts = df["offsetTime"].dropna()
    if len(ts) >= 2:
        stats["duration_s"]   = round(float(ts.max() - ts.min()), 2)
        stats["duration_min"] = round(stats["duration_s"] / 60, 2)
    else:
        stats["duration_s"]   = None
        stats["duration_min"] = None

    gps = df[df["latitude"].notna() & df["longitude"].notna() &
             (df["latitude"].abs() > 0.001)]
    stats["gps_points"] = int(len(gps))
    if len(gps) > 0:
        stats["lat_min"] = round(float(gps["latitude"].min()), 6)
        stats["lat_max"] = round(float(gps["latitude"].max()), 6)
        stats["lon_min"] = round(float(gps["longitude"].min()), 6)
        stats["lon_max"] = round(float(gps["longitude"].max()), 6)

    alt_col = "altitude" if "altitude" in df.columns and df["altitude"].notna().sum() > 0 else "baroAlt"
    if alt_col in df.columns:
        alt = df[alt_col].dropna()
        if len(alt) > 0:
            stats["alt_max_m"] = round(float(alt.max()), 2)
            stats["alt_min_m"] = round(float(alt.min()), 2)

    if "velH" in df.columns:
        vh = df["velH"].dropna()
        if len(vh) > 0:
            stats["max_speed_ms"]  = round(float(vh.max()), 2)
            stats["max_speed_kmh"] = round(float(vh.max() * 3.6), 1)
            stats["avg_speed_kmh"] = round(float(vh.mean() * 3.6), 1)

    if "totalVolts" in df.columns:
        v = df["totalVolts"].dropna()
        if len(v) > 0:
            stats["volt_start"] = round(float(v.iloc[0]), 3)
            stats["volt_end"]   = round(float(v.iloc[-1]), 3)
            stats["volt_drop"]  = round(float(v.iloc[0] - v.iloc[-1]), 3)

    if "percentageCapacity" in df.columns:
        pc = df["percentageCapacity"].dropna()
        if len(pc) > 0:
            stats["battery_start_pct"] = round(float(pc.max()), 1)
            stats["battery_end_pct"]   = round(float(pc.min()), 1)

    return stats


# ════════════════════════════════════════════════════════════════════════════
#  MASTER RUNNER
# ════════════════════════════════════════════════════════════════════════════
def run_all_checks(filepath):
    df = load_data(filepath)

    anomalies = []
    anomalies += check_timestamps(df)
    anomalies += check_gps(df)
    anomalies += check_statistics(df)

    seen = set()
    unique = []
    for a in anomalies:
        key = (a["row"], a["type"])
        if key not in seen:
            seen.add(key)
            unique.append(a)

    anomalies = sorted(unique, key=lambda x: (
        {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}.get(x["severity"], 3),
        x["row"]
    ))

    sev_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0}
    for a in anomalies:
        sev_counts[a["severity"]] = sev_counts.get(a["severity"], 0) + 1

    summary = {}
    for a in anomalies:
        cat = a["type"].split(" — ")[0]
        summary[cat] = summary.get(cat, 0) + 1

    if sev_counts["CRITICAL"] > 0 or sev_counts["HIGH"] > 5:
        verdict = "TAMPERED"
    elif sev_counts["HIGH"] > 0 or sev_counts["CRITICAL"] > 0 or len(anomalies) > 5:
        verdict = "SUSPICIOUS"
    else:
        verdict = "CLEAN"

    return {
        "df":           df,
        "anomalies":    anomalies,
        "summary":      summary,
        "sev_counts":   sev_counts,
        "verdict":      verdict,
        "total_rows":   len(df),
        "filepath":     filepath,
        "flight_stats": compute_flight_stats(df),
    }


# ════════════════════════════════════════════════════════════════════════════
#  EXPORT
# ════════════════════════════════════════════════════════════════════════════
def export_report(results, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "tamper_report.csv")
    rows = [{k: v for k, v in a.items()} for a in results["anomalies"]]
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    txt_path = os.path.join(out_dir, "tamper_summary.txt")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(txt_path, "w") as f:
        f.write("=" * 65 + "\n")
        f.write("    DRONE FLIGHT DATA — TAMPER DETECTION REPORT\n")
        f.write("=" * 65 + "\n\n")
        f.write(f"Generated : {now}\n")
        f.write(f"File      : {results['filepath']}\n")
        f.write(f"Total rows: {results['total_rows']:,}\n\n")
        f.write(f"VERDICT   : *** {results['verdict']} ***\n\n")
        f.write("-" * 65 + "\n")
        f.write("SEVERITY BREAKDOWN\n")
        f.write("-" * 65 + "\n")
        for sev in ["CRITICAL", "HIGH", "MEDIUM"]:
            f.write(f"  {sev:<12} {results['sev_counts'].get(sev, 0)}\n")
        f.write(f"  {'TOTAL':<12} {len(results['anomalies'])}\n\n")
        f.write("-" * 65 + "\n")
        f.write("CATEGORY BREAKDOWN\n")
        f.write("-" * 65 + "\n")
        for cat, cnt in results["summary"].items():
            f.write(f"  {cat} : {cnt}\n")
        f.write("\n" + "-" * 65 + "\n")
        f.write("FULL ANOMALY LIST\n")
        f.write("-" * 65 + "\n")
        for a in results["anomalies"]:
            f.write(f"\n[{a['severity']}] Row {a['row']}  t={a.get('offsetTime', '')}\n")
            f.write(f"  Type  : {a['type']}\n")
            f.write(f"  Detail: {a['detail']}\n")
            lat, lon = a.get("latitude"), a.get("longitude")
            if lat and lon:
                f.write(f"  GPS   : {lat:.6f}, {lon:.6f}\n")
    return csv_path, txt_path


# ════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════════════════
def _anomaly(row, df, atype, severity, detail):
    try:
        lat = float(df.at[row, "latitude"])  if "latitude"   in df.columns else None
        lon = float(df.at[row, "longitude"]) if "longitude"  in df.columns else None
        ts  = float(df.at[row, "offsetTime"])if "offsetTime" in df.columns else None
    except Exception:
        lat = lon = ts = None
    def safe(v):
        if v is None: return None
        try: return None if math.isnan(v) else v
        except: return None
    return {
        "row":        int(row),
        "offsetTime": safe(ts),
        "latitude":   safe(lat),
        "longitude":  safe(lon),
        "type":       atype,
        "severity":   severity,
        "detail":     detail,
    }
