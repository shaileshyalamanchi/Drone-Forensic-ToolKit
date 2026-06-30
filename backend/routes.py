"""
routes.py — All Flask API endpoints for DroneGuard
"""

import os
import uuid
import json
import math
from flask import request, jsonify, send_file, current_app
from werkzeug.utils import secure_filename

from tamper_engine import run_all_checks, export_report
from map_generator  import generate_map

ALLOWED_EXT = {".csv"}


def _allowed(filename):
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXT


def register_routes(app):

    # ── POST /api/upload ────────────────────────────────────────────────
    @app.route("/api/upload", methods=["POST"])
    def upload():
        """
        Accepts a multipart/form-data CSV upload.
        Saves file, returns a session_id used for all subsequent calls.
        """
        if "file" not in request.files:
            return jsonify({"error": "No file part in request"}), 400

        f = request.files["file"]
        if f.filename == "":
            return jsonify({"error": "No file selected"}), 400
        if not _allowed(f.filename):
            return jsonify({"error": "Only CSV files are accepted"}), 400

        session_id = str(uuid.uuid4())
        safe_name  = secure_filename(f.filename)
        upload_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], session_id)
        os.makedirs(upload_dir, exist_ok=True)
        filepath   = os.path.join(upload_dir, safe_name)
        f.save(filepath)

        return jsonify({
            "session_id":    session_id,
            "original_name": safe_name,
            "message":       "File uploaded successfully"
        }), 200


    # ── POST /api/analyse ───────────────────────────────────────────────
    @app.route("/api/analyse", methods=["POST"])
    def analyse():
        """
        Runs all tamper checks on the uploaded CSV.
        Body JSON: { "session_id": "..." }
        Returns full analysis results as JSON.
        """
        body = request.get_json(silent=True) or {}
        session_id = body.get("session_id", "")

        if not session_id:
            return jsonify({"error": "session_id is required"}), 400

        upload_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], session_id)
        if not os.path.isdir(upload_dir):
            return jsonify({"error": "Session not found. Please re-upload the file."}), 404

        csv_files = [f for f in os.listdir(upload_dir) if f.endswith(".csv")]
        if not csv_files:
            return jsonify({"error": "CSV file not found in session"}), 404

        filepath   = os.path.join(upload_dir, csv_files[0])
        report_dir = os.path.join(current_app.config["REPORTS_FOLDER"], session_id)
        os.makedirs(report_dir, exist_ok=True)

        try:
            results  = run_all_checks(filepath)
            map_path = generate_map(results["df"], results["anomalies"], report_dir)
            csv_rpt, txt_rpt = export_report(results, report_dir)

            response = {
                "session_id":    session_id,
                "verdict":       results["verdict"],
                "total_rows":    results["total_rows"],
                "anomaly_count": len(results["anomalies"]),
                "sev_counts":    results["sev_counts"],
                "summary":       results["summary"],
                "anomalies":     _safe_anomalies(results["anomalies"]),
                "flight_stats":  results.get("flight_stats", {}),
            }
            return jsonify(response), 200

        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500


    # ── GET /api/map/<session_id> ───────────────────────────────────────
    @app.route("/api/map/<session_id>", methods=["GET"])
    def get_map(session_id):
        """Serves the generated Folium HTML map file."""
        map_path = os.path.join(
            current_app.config["REPORTS_FOLDER"], session_id, "flight_map.html"
        )
        if not os.path.exists(map_path):
            return jsonify({"error": "Map not found. Run analysis first."}), 404
        return send_file(map_path, mimetype="text/html")


    # ── GET /api/report/<session_id>/<filetype> ─────────────────────────
    @app.route("/api/report/<session_id>/<filetype>", methods=["GET"])
    def get_report(session_id, filetype):
        """
        Download report files.
        filetype: 'csv' or 'txt'
        """
        names = {"csv": "tamper_report.csv", "txt": "tamper_summary.txt"}
        if filetype not in names:
            return jsonify({"error": "Invalid filetype. Use csv or txt"}), 400

        path = os.path.join(
            current_app.config["REPORTS_FOLDER"], session_id, names[filetype]
        )
        if not os.path.exists(path):
            return jsonify({"error": "Report not found. Run analysis first."}), 404

        return send_file(path, as_attachment=True, download_name=names[filetype])


    # ── GET /api/health ─────────────────────────────────────────────────
    @app.route("/api/health", methods=["GET"])
    def health():
        """Simple health check — frontend polls this on load."""
        return jsonify({"status": "ok", "service": "DroneGuard API v1.0"}), 200


# ── Helpers ─────────────────────────────────────────────────────────────────
def _safe_anomalies(anomalies):
    """Convert anomalies list to JSON-safe format (handle NaN/None/floats)."""
    safe = []
    for a in anomalies:
        row = {}
        for k, v in a.items():
            if v is None:
                row[k] = None
            elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                row[k] = None
            else:
                row[k] = v
        safe.append(row)
    return safe
