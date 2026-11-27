"""
Smart Robotic Arm – Object Detection & Monitoring Web Application
Flask backend with SQLite database, ESP32-CAM image reception, and REST API.
"""

import os
import time
import sqlite3
from datetime import datetime
from flask import (
    Flask, render_template, request, jsonify, send_from_directory, redirect, url_for
)
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
DATABASE = os.path.join(BASE_DIR, "database.db")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "bmp"}

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max upload

# Track ESP32-CAM heartbeat (epoch seconds)
esp32_last_seen: float = 0.0
ESP32_TIMEOUT = 15  # seconds – consider disconnected after this

# Current pickup status
current_pickup_status: str = "Not Picked"

# Scanning mode – when False the ESP32 should not capture / frontend should not poll
scanning_active: bool = False


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    """Return a new database connection (with Row factory)."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the pickup_history table if it doesn't exist."""
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pickup_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            image_path      TEXT    NOT NULL,
            pickup_status   TEXT    NOT NULL DEFAULT 'Picked',
            captured_date   TEXT    NOT NULL,
            captured_time   TEXT    NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def is_esp32_connected() -> bool:
    return (time.time() - esp32_last_seen) < ESP32_TIMEOUT


# ---------------------------------------------------------------------------
# Web routes
# ---------------------------------------------------------------------------
@app.route("/")
def dashboard():
    """Render the main dashboard."""
    conn = get_db()
    latest = conn.execute(
        "SELECT * FROM pickup_history ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return render_template(
        "dashboard.html",
        connected=is_esp32_connected(),
        pickup_status=current_pickup_status,
        latest=latest,
        scanning=scanning_active,
    )


@app.route("/history")
def history():
    """Render the pickup history page."""
    search = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip()

    query = "SELECT * FROM pickup_history WHERE 1=1"
    params: list = []

    if search:
        query += " AND (captured_date LIKE ? OR captured_time LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if status_filter:
        query += " AND pickup_status = ?"
        params.append(status_filter)

    query += " ORDER BY id DESC"

    conn = get_db()
    records = conn.execute(query, params).fetchall()
    conn.close()
    return render_template("history.html", records=records, search=search, status_filter=status_filter)


# ---------------------------------------------------------------------------
# API endpoints (consumed by ESP32-CAM and frontend JS)
# ---------------------------------------------------------------------------
@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    """ESP32-CAM calls this periodically to signal it is alive."""
    global esp32_last_seen
    esp32_last_seen = time.time()
    return jsonify({"status": "ok"}), 200


@app.route("/api/upload", methods=["POST"])
def upload_image():
    """
    Receive an image from ESP32-CAM after a successful pickup.
    Expects:
      • multipart/form-data with field 'image'
      • optional field 'pickup_status' (defaults to 'Picked')
    """
    global esp32_last_seen, current_pickup_status
    esp32_last_seen = time.time()

    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400

    file = request.files["image"]
    if file.filename == "" or not allowed_file(file.filename):
        return jsonify({"error": "Invalid or missing filename"}), 400

    # Build a unique filename
    now = datetime.now()
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = secure_filename(f"pickup_{timestamp_str}.{ext}")
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    pickup_status = request.form.get("pickup_status", "Picked")
    current_pickup_status = pickup_status

    # Store in database
    conn = get_db()
    conn.execute(
        "INSERT INTO pickup_history (image_path, pickup_status, captured_date, captured_time) VALUES (?, ?, ?, ?)",
        (
            f"uploads/{filename}",
            pickup_status,
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "ok", "filename": filename}), 200


@app.route("/api/status", methods=["GET"])
def api_status():
    """Return current connection + pickup status (polled by dashboard JS)."""
    conn = get_db()
    latest = conn.execute(
        "SELECT * FROM pickup_history ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    latest_dict = None
    if latest:
        latest_dict = {
            "id": latest["id"],
            "image_path": latest["image_path"],
            "pickup_status": latest["pickup_status"],
            "captured_date": latest["captured_date"],
            "captured_time": latest["captured_time"],
        }

    return jsonify(
        {
            "connected": is_esp32_connected(),
            "pickup_status": current_pickup_status,
            "latest": latest_dict,
            "scanning": scanning_active,
        }
    )


@app.route("/api/history", methods=["GET"])
def api_history():
    """Return pickup history as JSON (for AJAX refresh)."""
    search = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip()

    query = "SELECT * FROM pickup_history WHERE 1=1"
    params: list = []

    if search:
        query += " AND (captured_date LIKE ? OR captured_time LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if status_filter:
        query += " AND pickup_status = ?"
        params.append(status_filter)

    query += " ORDER BY id DESC"

    conn = get_db()
    rows = conn.execute(query, params).fetchall()
    conn.close()

    records = [
        {
            "id": r["id"],
            "image_path": r["image_path"],
            "pickup_status": r["pickup_status"],
            "captured_date": r["captured_date"],
            "captured_time": r["captured_time"],
        }
        for r in rows
    ]
    return jsonify(records)


@app.route("/api/scanning", methods=["POST"])
def toggle_scanning():
    """Toggle scanning mode on/off from the dashboard button."""
    global scanning_active
    data = request.get_json(silent=True) or {}
    if "active" in data:
        scanning_active = bool(data["active"])
    else:
        scanning_active = not scanning_active
    return jsonify({"scanning": scanning_active}), 200


@app.route("/api/scanning", methods=["GET"])
def get_scanning():
    """ESP32 can poll this to know whether to capture."""
    return jsonify({"scanning": scanning_active}), 200


@app.route("/api/pickup_status", methods=["POST"])
def update_pickup_status():
    """ESP32 or sensor calls this to update pickup status."""
    global current_pickup_status, esp32_last_seen
    esp32_last_seen = time.time()
    data = request.get_json(silent=True) or {}
    current_pickup_status = data.get("pickup_status", "Not Picked")
    return jsonify({"status": "ok", "pickup_status": current_pickup_status}), 200


@app.route("/download/<path:filename>")
def download_file(filename):
    """Allow users to download a captured image."""
    return send_from_directory(
        app.config["UPLOAD_FOLDER"], filename, as_attachment=True
    )


# ---------------------------------------------------------------------------
# Start-up
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    init_db()
    print("=" * 60)
    print("  Smart Robotic Arm – Monitoring Web App")
    print("  Open http://127.0.0.1:5000 in your browser")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=True)
