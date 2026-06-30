# DroneGuard — Drone Flight Data Tamper Detection

A forensic tool for law enforcement to detect tampering in DJI drone flight logs.

## Quick Start

### 1. Install Python dependencies
```bash
cd backend
pip install -r requirements.txt
```

### 2. Start the backend
```bash
cd backend
python app.py
```
You should see:
```
==================================================
  DroneGuard — Tamper Detection Backend
  Running on http://localhost:5000
  Open frontend/index.html in your browser
==================================================
```

### 3. Open the frontend
Double-click `frontend/index.html` to open it in your browser.

---

## How to Use

1. **Upload** — Click "Browse" or drag & drop your converted DJI CSV file
2. **Run Analysis** — Click "▶ Run Analysis" and wait (~15–30 seconds)
3. **Review Results** — Verdict, anomaly counts, and full anomaly table appear
4. **Download** — Open the interactive map, download CSV or TXT report

---

## Input File

The system accepts **CSV files converted from DJI .DAT flight logs**.
Use tools like DJI Flight Log Viewer or dat2csv to convert.

Required columns (any subset is fine — missing columns are skipped):
- `offsetTime`, `messageid` — timestamp/sequence checks
- `latitude`, `longitude` — GPS checks  
- `altitude`, `baroAlt` — altitude checks
- `accelX`, `accelY`, `accelZ`, `gyroX`, `gyroY`, `gyroZ` — IMU checks
- `totalVolts`, `velN`, `velE`, `velH` — battery/velocity checks

---

## Verdict Meanings

| Verdict | Meaning |
|---------|---------|
| ✅ CLEAN | All checks passed — no evidence of tampering |
| ⚠️ SUSPICIOUS | Anomalies detected — manual review recommended |
| 🔴 TAMPERED | Strong evidence of data modification found |

---

## Output Files

| File | Description |
|------|-------------|
| `flight_map.html` | Interactive Leaflet map with color-coded path and anomaly pins |
| `tamper_report.csv` | All anomalies in spreadsheet format |
| `tamper_summary.txt` | Court-ready narrative report |

---

## Project Structure

```
droneguard/
├── backend/
│   ├── app.py              ← Run this
│   ├── tamper_engine.py    ← Detection logic
│   ├── map_generator.py    ← Map generation
│   ├── routes.py           ← API endpoints
│   └── requirements.txt
├── frontend/
│   └── index.html          ← Open in browser
├── BACKEND_DOCS.md         ← Full technical documentation
└── README.md               ← This file
```

---

*DroneGuard v1.0  Built for Law Enforcement Use*
