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

app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB

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
    return subprocess.run(
        cmd, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


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


def render_scene(args):
    img   = args["img_path"]
    out   = args["out_path"]
    w, h  = args["w"], args["h"]
    crf   = args["crf"]
    fps   = args["fps"]
    dur   = args.get("duration", SCENE_DURATION)

    sw = int(w * 1.05)
    sh = int(h * 1.05)
    sw += sw % 2
    sh += sh % 2

    vf = (
        f"scale={sw}:{sh}:flags=fast_bilinear,"
        f"crop={w}:{h}:(iw-ow)/2:(ih-oh)/2,"
        f"setsar=1"
    )

    cmd = [
        "ffmpeg", "-y",
        "-threads", str(FFMPEG_THREADS),
        "-loop", "1",
        "-i", str(img),
        "-vf", vf,
        "-t", str(dur),
        "-r", str(fps),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", str(crf),
        "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-an",
        str(out),
    ]
    run(cmd)
    return out


def merge_with_xfade(scene_paths, audio_path, out_path, crf, fps):
    n = len(scene_paths)

    if n == 1:
        if audio_path:
            _mux_audio(scene_paths[0], audio_path, out_path)
        else:
            import shutil
            shutil.copy(scene_paths[0], out_path)
        return out_path

    inputs = []
    for sp in scene_paths:
        inputs += ["-i", str(sp)]

    filter_parts = []
    offset = SCENE_DURATION - TRANSITION_DUR
    prev = "0:v"
    for i in range(1, n):
        lbl = f"v{i}" if i < n - 1 else "vout"
        filter_parts.append(
            f"[{prev}][{i}:v]xfade=transition=fade:"
            f"duration={TRANSITION_DUR}:offset={offset:.3f}[{lbl}]"
        )
        prev = lbl
        offset += SCENE_DURATION - TRANSITION_DUR

    merged = WORK_DIR / f"{uuid.uuid4().hex}_merged.mp4"
    cmd = (
        ["ffmpeg", "-y", "-threads", str(FFMPEG_THREADS * 2)]
        + inputs
        + [
            "-filter_complex", ";".join(filter_parts),
            "-map", "[vout]",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-movflags", "+faststart",
            str(merged),
        ]
    )
    run(cmd)

    if audio_path:
        _mux_audio(merged, audio_path, out_path)
    else:
        merged.rename(out_path)

    return out_path


def _mux_audio(video, audio, out):
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video),
        "-i", str(audio),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "96k",
        "-shortest",
        "-movflags", "+faststart",
        str(out),
    ]
    run(cmd)


def ai_scene_plan(num_scenes, prompt=""):
    if not GEMINI_API_KEY or not _GENAI_OK:
        return [{}] * num_scenes
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp  = model.generate_content(
            f"You are a cinematic director. For {num_scenes} scenes"
            + (f" about: {prompt}" if prompt else "")
            + ". Return ONLY a JSON array like "
            '[{"motion":"zoom_in"},{"motion":"pan_left"},...] '
            "using: zoom_in, zoom_out, pan_left, pan_right, pan_up, pan_down, static."
        )
        import json, re
        m = re.search(r"\[.*?\]", resp.text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        print(f"Gemini fallback: {e}")
    return [{}] * num_scenes


@app.after_request
def cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/health", methods=["GET", "OPTIONS"])
def health():
    if request.method == "OPTIONS":
        return make_response("", 204)
    return jsonify({"status": "ok", "version": "v3-fixed"})


@app.route("/generate-video", methods=["POST", "OPTIONS"])
def generate_video():
    if request.method == "OPTIONS":
        return make_response("", 204)

    t0   = time.time()
    data = request.get_json(force=True)

    images    = data.get("images", [])
    audio_src = data.get("audio")
    quality   = data.get("quality", "480p")
    prompt    = data.get("prompt", "")
    scene_dur = float(data.get("scene_duration", SCENE_DURATION))

    if not images:
        return jsonify({"error": "No images provided"}), 400

    w, h, crf, _, fps = QUALITY.get(quality, QUALITY["480p"])

    sid  = uuid.uuid4().hex
    sdir = WORK_DIR / sid
    sdir.mkdir(exist_ok=True)

    try:
        img_paths = []
        for i, src in enumerate(images):
            ext = ".jpg"
            p = b64_to_temp(src, ext) if src.startswith("data:") else download_to_temp(src, ext)
            img_paths.append(p)

        audio_path = None
        if audio_src:
            audio_path = (
                b64_to_temp(audio_src, ".mp3")
                if audio_src.startswith("data:")
                else download_to_temp(audio_src, ".mp3")
            )

        plan = ai_scene_plan(len(img_paths), prompt)

        render_args = []
        scene_paths = []
        for i, (img_p, p) in enumerate(zip(img_paths, plan)):
            outp = sdir / f"s{i:03d}.mp4"
            scene_paths.append(outp)
            render_args.append({
                "img_path": img_p,
                "out_path": outp,
                "w": w, "h": h,
                "crf": crf, "fps": fps,
                "duration": scene_dur,
            })

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            list(ex.map(render_scene, render_args))

        final = sdir / "final.mp4"
        merge_with_xfade(scene_paths, audio_path, final, crf, fps)

        print(f"Done: {len(images)} scenes | {quality} | {time.time()-t0:.1f}s")

        resp = make_response(send_file(
            str(final),
            mimetype="video/mp4",
            as_attachment=True,
            download_name="directors_cut.mp4",
        ))
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    except subprocess.CalledProcessError as e:
        err = e.stderr.decode()[-500:] if e.stderr else ""
        print(f"FFmpeg error:\n{err}")
        return jsonify({"error": "FFmpeg failed", "details": err}), 500
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        import shutil
        shutil.rmtree(sdir, ignore_errors=True)


@app.route("/scene-plan", methods=["POST", "OPTIONS"])
def scene_plan_ep():
    if request.method == "OPTIONS":
        return make_response("", 204)
    data = request.get_json(force=True)
    plan = ai_scene_plan(int(data.get("num_scenes", 1)), data.get("prompt", ""))
    return jsonify({"plan": plan})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)
