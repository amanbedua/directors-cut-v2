"""
Directors Cut v2 — ULTRA FAST Backend (v3 fixed)
"""

import os
import uuid
import time
import tempfile
import subprocess
import concurrent.futures
from pathlib import Path
from flask import Flask, request, jsonify, send_file, make_response
from flask_cors import CORS
import requests
import base64

try:
    import google.generativeai as genai
    _GENAI_OK = True
except ImportError:
    genai = None
    _GENAI_OK = False

app = Flask(__name__)

CORS(app, resources={r"/*": {
    "origins": "*",
    "allow_headers": ["Content-Type", "Authorization"],
    "methods": ["GET", "POST", "OPTIONS"],
}})

app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_API_KEY and _GENAI_OK:
    genai.configure(api_key=GEMINI_API_KEY)

WORK_DIR = Path(tempfile.gettempdir()) / "dc_v3"
WORK_DIR.mkdir(exist_ok=True)

QUALITY = {
    "480p":  (854,  480,  35, "ultrafast", 15),
    "720p":  (1280, 720,  32, "ultrafast", 20),
    "1080p": (1920, 1080, 30, "superfast", 24),
}

SCENE_DURATION = 4
TRANSITION_DUR = 0.6
MAX_WORKERS    = 4
FFMPEG_THREADS = 2


def run(cmd):
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def download_to_temp(url, suffix):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    p = WORK_DIR / f"{uuid.uuid4().hex}{suffix}"
    p.write_bytes(r.content)
    return p


def b64_to_temp(b64, suffix):
    data = base64.b64decode(b64.split(",", 1)[-1])
    p = WORK_DIR / f"{uuid.uuid4().hex}{suffix}"
    p.write_bytes(data)
    return p


@app.after_request
def cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/generate-video", methods=["POST"])
def generate_video():
    try:
        data = request.get_json(force=True)
        images = data.get("images", [])

        if not images:
            return jsonify({"error": "No images"}), 400

        return jsonify({"status": "backend working"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
