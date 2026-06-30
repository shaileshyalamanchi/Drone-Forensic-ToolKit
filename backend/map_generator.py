"""
map_generator.py — Folium interactive HTML map generator
Colors flight path by speed and altitude, places anomaly markers.
"""

import os
import math
import numpy as np
import pandas as pd
import folium
from math import radians, sin, cos, sqrt, atan2


def _haversine(la1, lo1, la2, lo2):
    R = 6_371_000
    f1, f2 = radians(la1), radians(la2)
    dp = radians(la2 - la1)
    dl = radians(lo2 - lo1)
    a = sin(dp / 2) ** 2 + cos(f1) * cos(f2) * sin(dl / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _speed_color(norm):
    """Blue → Cyan → Yellow → Red gradient for speed."""
    norm = max(0.0, min(1.0, norm))
    if norm < 0.33:
        t = norm / 0.33
        r, g, b = 0, int(t * 180), 220
    elif norm < 0.66:
        t = (norm - 0.33) / 0.33
        r, g, b = int(t * 255), 200, int(220 - t * 220)
    else:
        t = (norm - 0.66) / 0.34
        r, g, b = 255, int(200 - t * 200), 0
    return f"#{r:02x}{g:02x}{b:02x}"


def _alt_color(norm):
    """Green → Teal → Blue gradient for altitude."""
    norm = max(0.0, min(1.0, norm))
    if norm < 0.5:
        t = norm * 2
        r, g, b = 0, int(160 - t * 60), int(t * 200)
    else:
        t = (norm - 0.5) * 2
        r, g, b = 0, int(100 - t * 100), 200
    return f"#{r:02x}{g:02x}{b:02x}"


def generate_map(df, anomalies, out_dir):
    """
    Build and save flight_map.html.
    Returns absolute path to saved file.
    """
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "flight_map.html")

    # Filter valid GPS rows
    gps = df[
        df["latitude"].notna() & df["longitude"].notna() &
        df["offsetTime"].notna() &
        (df["latitude"].abs() > 0.001) &
        (df["longitude"].abs() > 0.001)
    ].copy()

    if len(gps) < 2:
        m = folium.Map(location=[20.0, 78.0], zoom_start=5)
        folium.Marker(
            [20, 78],
            popup="No valid GPS data found",
            icon=folium.Icon(color="red")
        ).add_to(m)
        m.save(out_path)
        return out_path

    lat_c = float(gps["latitude"].mean())
    lon_c = float(gps["longitude"].mean())

    m = folium.Map(location=[lat_c, lon_c], zoom_start=17, tiles="OpenStreetMap")

    # Satellite layer
    folium.TileLayer(
        tiles=(
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        attr="Esri World Imagery",
        name="Satellite",
        overlay=False,
        control=True,
    ).add_to(m)

    # Downsample for browser performance (max 8000 segments)
    step = max(1, len(gps) // 8000)
    gps_ds = gps.iloc[::step].reset_index(drop=False)

    lats  = gps_ds["latitude"].values
    lons  = gps_ds["longitude"].values
    times = gps_ds["offsetTime"].values

    # Compute inter-point speed
    speeds = [0.0]
    for i in range(1, len(gps_ds)):
        dt = times[i] - times[i - 1]
        if dt > 0:
            d = _haversine(lats[i-1], lons[i-1], lats[i], lons[i])
            speeds.append(d / dt)
        else:
            speeds.append(0.0)
    speeds = np.array(speeds)

    sp_min, sp_max = float(np.nanmin(speeds)), float(np.nanmax(speeds))

    # Pick altitude column
    alt_col = "altitude"
    if "altitude" not in gps_ds.columns or gps_ds["altitude"].notna().sum() == 0:
        alt_col = "baroAlt" if "baroAlt" in gps_ds.columns else None

    if alt_col and alt_col in gps_ds.columns:
        alts = gps_ds[alt_col].values.astype(float)
    else:
        alts = np.zeros(len(gps_ds))

    valid_alts = alts[~np.isnan(alts)]
    alt_min = float(valid_alts.min()) if len(valid_alts) > 0 else 0.0
    alt_max = float(valid_alts.max()) if len(valid_alts) > 0 else 1.0

    # Speed layer
    sp_group = folium.FeatureGroup(name="🎨 Path — Speed", show=True)
    for i in range(1, len(gps_ds)):
        norm  = (speeds[i] - sp_min) / (sp_max - sp_min + 1e-9)
        color = _speed_color(norm)
        spd_kmh = speeds[i] * 3.6
        folium.PolyLine(
            [(lats[i-1], lons[i-1]), (lats[i], lons[i])],
            color=color, weight=4, opacity=0.9,
            tooltip=f"Speed: {spd_kmh:.1f} km/h"
        ).add_to(sp_group)
    sp_group.add_to(m)

    # Altitude layer
    alt_group = folium.FeatureGroup(name="🏔 Path — Altitude", show=False)
    for i in range(1, len(gps_ds)):
        a_val = alts[i] if not math.isnan(float(alts[i])) else alt_min
        norm  = (a_val - alt_min) / (alt_max - alt_min + 1e-9)
        color = _alt_color(norm)
        folium.PolyLine(
            [(lats[i-1], lons[i-1]), (lats[i], lons[i])],
            color=color, weight=4, opacity=0.9,
            tooltip=f"Altitude: {a_val:.1f} m"
        ).add_to(alt_group)
    alt_group.add_to(m)

    # Start / End markers
    first = gps.iloc[0]
    last  = gps.iloc[-1]

    folium.Marker(
        [float(first["latitude"]), float(first["longitude"])],
        popup=folium.Popup(
            f"<b>FLIGHT START</b><br>Time: {first.get('offsetTime', '?')}s<br>"
            f"Lat: {first['latitude']:.6f}<br>Lon: {first['longitude']:.6f}",
            max_width=220
        ),
        icon=folium.Icon(color="green", icon="play", prefix="fa")
    ).add_to(m)

    folium.Marker(
        [float(last["latitude"]), float(last["longitude"])],
        popup=folium.Popup(
            f"<b>FLIGHT END</b><br>Time: {last.get('offsetTime', '?')}s<br>"
            f"Lat: {last['latitude']:.6f}<br>Lon: {last['longitude']:.6f}",
            max_width=220
        ),
        icon=folium.Icon(color="red", icon="stop", prefix="fa")
    ).add_to(m)

    # Anomaly markers
    sev_color = {"CRITICAL": "darkred", "HIGH": "red", "MEDIUM": "orange"}
    sev_icon  = {
        "CRITICAL": "exclamation-triangle",
        "HIGH": "exclamation-circle",
        "MEDIUM": "exclamation"
    }
    anom_group = folium.FeatureGroup(name="⚠ Anomaly Markers", show=True)

    for a in anomalies:
        lat = a.get("latitude")
        lon = a.get("longitude")
        if lat is None or lon is None:
            continue
        try:
            flat, flon = float(lat), float(lon)
            if math.isnan(flat) or math.isnan(flon):
                continue
            if abs(flat) < 0.001 or abs(flon) < 0.001:
                continue
        except (TypeError, ValueError):
            continue

        col = sev_color.get(a["severity"], "orange")
        ico = sev_icon.get(a["severity"], "exclamation")
        sev_color_hex = (
            "darkred" if a["severity"] == "CRITICAL"
            else "red" if a["severity"] == "HIGH"
            else "darkorange"
        )
        popup_html = (
            f"<div style='font-family:sans-serif;min-width:220px'>"
            f"<b style='color:{sev_color_hex}'>[{a['severity']}]</b><br>"
            f"<b>{a['type']}</b><br>"
            f"<hr style='margin:4px 0'>"
            f"Row: {a['row']}<br>"
            f"Time: {a.get('offsetTime', 'N/A')}<br>"
            f"<small style='color:#444'>{a['detail']}</small>"
            f"</div>"
        )
        folium.Marker(
            [flat, flon],
            popup=folium.Popup(popup_html, max_width=300),
            icon=folium.Icon(color=col, icon=ico, prefix="fa")
        ).add_to(anom_group)

    anom_group.add_to(m)

    # Legend
    legend = """
    <div style="position:fixed;bottom:30px;right:10px;z-index:9999;
                background:white;padding:14px 18px;border-radius:10px;
                border:1px solid #ccc;font-family:Arial,sans-serif;
                font-size:12px;line-height:1.8;
                box-shadow:0 2px 10px rgba(0,0,0,.2)">
      <b style="font-size:13px">Speed (km/h)</b><br>
      <span style="color:#0000dc">&#9644;</span> Slow &nbsp;
      <span style="color:#c8c800">&#9644;</span> Medium &nbsp;
      <span style="color:#ff0000">&#9644;</span> Fast<br>
      <b style="font-size:13px">Anomalies</b><br>
      <span style="color:darkred">&#9679;</span> Critical &nbsp;
      <span style="color:red">&#9679;</span> High &nbsp;
      <span style="color:orange">&#9679;</span> Medium<br>
      <span style="color:green">&#9654;</span> Start &nbsp;
      <span style="color:red">&#9646;</span> End
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))
    folium.LayerControl(collapsed=False).add_to(m)

    m.save(out_path)
    return out_path
