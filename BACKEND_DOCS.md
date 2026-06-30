# DroneGuard — Backend Architecture & Logic

## Project Overview

DroneGuard is a forensic tool for law enforcement to detect tampering in DJI drone flight logs. It accepts a converted CSV file (from .DAT format) and runs three independent tamper-detection methods, generating an interactive map and downloadable reports.

---

## File Structure

```
droneguard/
├── backend/
│   ├── app.py              ← Flask entry point
│   ├── routes.py           ← All API endpoints
│   ├── tamper_engine.py    ← Core detection logic (3 methods)
│   ├── map_generator.py    ← Folium interactive map
│   ├── requirements.txt    ← Python dependencies
│   ├── uploads/            ← Uploaded CSVs (auto-created)
│   └── reports/            ← Generated reports (auto-created)
└── frontend/
    └── index.html          ← Single-file UI (HTML + CSS + JS)
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET  | `/api/health` | Health check — frontend polls on load |
| POST | `/api/upload` | Upload CSV → returns `session_id` |
| POST | `/api/analyse` | Run full analysis → returns JSON results |
| GET  | `/api/map/<session_id>` | Serve generated Folium HTML map |
| GET  | `/api/report/<session_id>/csv` | Download anomaly CSV report |
| GET  | `/api/report/<session_id>/txt` | Download narrative TXT report |

---

## Session Flow

```
[Browser]                        [Flask Backend]
   │                                  │
   ├── POST /api/upload (CSV file) ──►│ Save file, return session_id
   │◄── { session_id: "uuid" } ───────┤
   │                                  │
   ├── POST /api/analyse ────────────►│ run_all_checks() → generate_map() → export_report()
   │◄── { verdict, anomalies, ... } ──┤
   │                                  │
   ├── GET /api/map/<id> ────────────►│ Serve flight_map.html
   └── GET /api/report/<id>/csv ─────►│ Download tamper_report.csv
```

---

## Detection Methods

### Method 1 — Timestamp Integrity (`check_timestamps`)

**Column used:** `offsetTime`, `messageid`

| Check | Severity | Logic |
|-------|----------|-------|
| Backward timestamp | CRITICAL | `offsetTime[i] < offsetTime[i-1]` — time cannot go backwards |
| Large time gap | HIGH | Gap > 2 seconds between consecutive rows (normal = ~5ms) |
| Duplicate timestamp | HIGH | Same time value appears more than once with different sensor values |
| MessageID jump | HIGH/MEDIUM | DJI increments `messageid` by 3 per packet; any other delta = missing/inserted packets |

**Why it works:** DJI firmware writes packets in strict monotonic time order. Any backward jump, gap, or duplicate is a strong indicator of row deletion or insertion.

---

### Method 2 — GPS Consistency (`check_gps`)

**Columns used:** `latitude`, `longitude`, `offsetTime`, `altitude`, `baroAlt`, `satnum`

| Check | Severity | Logic |
|-------|----------|-------|
| Impossible speed | CRITICAL | Haversine distance / time > 55.6 m/s (200 km/h) |
| Altitude jump | HIGH | Altitude changes > 50m in a single sample step |
| Baro/GPS mismatch | MEDIUM | GPS altitude vs baroAlt differ by > 30m |
| Satellite loss | MEDIUM | `satnum == 0` while drone is airborne (altitude > 1m) |

**Haversine Formula:**
```
a = sin²(Δlat/2) + cos(lat1) · cos(lat2) · sin²(Δlon/2)
d = 2R · atan2(√a, √(1-a))
speed = d / Δtime
```

**Why it works:** Editing GPS coordinates without adjusting timestamps creates physically impossible flight speeds. Altitude tampering is caught by cross-referencing GPS altitude with the independent barometric altimeter.

---

### Method 3 — Statistical Analysis (`check_statistics`)

**Columns used:** `accelX/Y/Z`, `gyroX/Y/Z`, `totalVolts`, `velN`, `velE`, `velH`, `rFront`, `lFront`, `lBack`, `rBack`

| Check | Severity | Logic |
|-------|----------|-------|
| IMU spike | MEDIUM | Z-score > 6.0 on any accelerometer/gyro axis |
| Battery voltage rise | HIGH | `totalVolts[i] > totalVolts[i-1] + 0.1V` — voltage can only fall during flight |
| Velocity inconsistency | MEDIUM | `velH ≠ √(velN² + velE²)` by > 5 m/s |
| Motor RPM imbalance | MEDIUM | Max motor RPM > 35% above mean of all 4 motors |

**Z-score formula:**
```
z = |x - μ| / σ
Flag if z > 6.0 (practically impossible in normal flight data)
```

**Why it works:** Replacing GPS coordinates while leaving IMU/motor data unchanged creates internal inconsistencies. Battery voltage is a one-way chemical process — any voltage increase indicates a data splice.

---

## Verdict Logic

```python
if CRITICAL > 0 or HIGH > 10:
    verdict = "TAMPERED"
elif HIGH > 0 or total_anomalies > 5:
    verdict = "SUSPICIOUS"
else:
    verdict = "CLEAN"
```

---

## Map Generation (`map_generator.py`)

Built using **Folium** (Python wrapper for Leaflet.js):

1. **GPS downsampling** — up to 8,000 segments rendered for browser performance
2. **Speed layer** — path colored Blue→Yellow→Red by computed inter-point speed
3. **Altitude layer** — path colored Green→Blue by altitude value
4. **Anomaly markers** — pin dropped at GPS coordinate of each anomaly, color-coded by severity
5. **Layer control** — toggle speed/altitude/anomaly overlays
6. **Satellite tiles** — Esri World Imagery available as alternative basemap

---

## Report Outputs

### `tamper_report.csv`
One row per anomaly with columns: `row, offsetTime, latitude, longitude, type, severity, detail`

### `tamper_summary.txt`
Human-readable narrative including:
- Generation timestamp
- Source file path
- Verdict
- Severity breakdown table
- Category breakdown
- Full anomaly list with GPS coordinates

---

## Setup & Run

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start backend
python app.py

# 3. Open frontend
# Open frontend/index.html in any browser (double-click)
# Backend runs on http://localhost:5000
```

### Dependencies
| Package | Purpose |
|---------|---------|
| flask | Web framework for REST API |
| flask-cors | Cross-origin headers for browser requests |
| pandas | CSV loading and data manipulation |
| numpy | Vectorised numerical operations |
| folium | Interactive Leaflet.js map generation |
| scipy | (Available for future statistical extensions) |
| werkzeug | Secure filename handling |

---

## Performance Notes

- Tested on 408,780-row DJI CSV files (~50MB)
- Analysis time: ~15–30 seconds depending on hardware
- Map generation: downsampled to 8,000 segments for smooth browser rendering
- Memory usage: ~200–400MB for large flight logs

---

## Security Notes

- Sessions are UUID-isolated — each upload gets a unique folder
- Files are saved with `werkzeug.secure_filename` to prevent path traversal
- CORS restricted to `/api/*` routes only
- Max upload size: 500MB
- No data is sent to external servers — fully offline capable

---

*DroneGuard v1.0 — Built for Law Enforcement Use*
