"""
app.py — Flask API entry point
DroneGuard: Drone Flight Data Tamper Detection System
"""

import os
from flask import Flask
from flask_cors import CORS
from routes import register_routes

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR  = os.path.join(BASE_DIR, "uploads")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")

os.makedirs(UPLOAD_DIR,  exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

app = Flask(__name__)
app.config["UPLOAD_FOLDER"]      = UPLOAD_DIR
app.config["REPORTS_FOLDER"]     = REPORTS_DIR
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024   # 500 MB max upload

CORS(app, resources={r"/api/*": {"origins": "*"}})

register_routes(app)

if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  DroneGuard — Tamper Detection Backend")
    print("  Running on http://localhost:5000")
    print("  Open frontend/index.html in your browser")
    print("=" * 50 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
