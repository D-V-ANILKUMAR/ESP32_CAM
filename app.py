"""
Smart Robotic Arm – Object Detection & Monitoring Web Application
Flask backend with PostgreSQL (Neon) database, ESP32-CAM image reception, and REST API.
"""

import os
import time
import psycopg2
import psycopg2.extras
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
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "bmp"}

# PostgreSQL connection string (Neon)
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_ufSi7v1tZxVC@ep-withered-frog-be1kjlqi-pooler.c-9.us-east-2.aws.neon.tech/neondb?sslmode=require"
)

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max upload
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

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
    """Return a new PostgreSQL connection (with RealDictCursor)."""
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def init_db():
    """Create the pickup_history table if it doesn't exist."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pickup_history (
            id              SERIAL PRIMARY KEY,
            image_path      TEXT    NOT NULL,
            pickup_status   TEXT    NOT NULL DEFAULT 'Picked',
            captured_date   TEXT    NOT NULL,
            captured_time   TEXT    NOT NULL
        )
        """
    )
    conn.commit()
    cur.close()
    conn.close()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def is_esp32_connected() -> bool:
    return (time.time() - esp32_last_seen) < ESP32_TIMEOUT


def row_to_dict(row, cur):
    """Convert a psycopg2 row tuple to a dict using cursor description."""
    if row is None:
        return None
    columns = [desc[0] for desc in cur.description]
    return dict(zip(columns, row))


# ---------------------------------------------------------------------------
# Web routes
# ---------------------------------------------------------------------------
@app.route("/")
def dashboard():
    """Render the main dashboard."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM pickup_history ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    latest = row_to_dict(row, cur)
    cur.close()
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
    param_idx = 1

    if search:
        query += f" AND (captured_date LIKE %s OR captured_time LIKE %s)"
        params += [f"%{search}%", f"%{search}%"]
    if status_filter:
        query += f" AND pickup_status = %s"
        params.append(status_filter)

    query += " ORDER BY id DESC"

    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]
    records = [dict(zip(columns, row)) for row in rows]
    cur.close()
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
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pickup_history (image_path, pickup_status, captured_date, captured_time) VALUES (%s, %s, %s, %s)",
        (
            f"uploads/{filename}",
            pickup_status,
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "ok", "filename": filename}), 200


@app.route("/api/status", methods=["GET"])
def api_status():
    """Return current connection + pickup status (polled by dashboard JS)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM pickup_history ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    latest = row_to_dict(row, cur)
    cur.close()
    conn.close()

    return jsonify(
        {
            "connected": is_esp32_connected(),
            "pickup_status": current_pickup_status,
            "latest": latest,
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
        query += " AND (captured_date LIKE %s OR captured_time LIKE %s)"
        params += [f"%{search}%", f"%{search}%"]
    if status_filter:
        query += " AND pickup_status = %s"
        params.append(status_filter)

    query += " ORDER BY id DESC"

    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]
    records = [dict(zip(columns, row)) for row in rows]
    cur.close()
    conn.close()

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
# Start-up (runs on import – needed for gunicorn / Render)
# ---------------------------------------------------------------------------
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
init_db()

if __name__ == "__main__":
    print("=" * 60)
    print("  Smart Robotic Arm – Monitoring Web App")
    print("  Open http://127.0.0.1:5000 in your browser")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=True)
