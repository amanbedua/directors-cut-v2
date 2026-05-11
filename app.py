"""
Directors Cut v2 — HF Space Backend (Production v2)
POST /generate-video  → { job_id }        (starts async job)
GET  /status/<job_id> → { status, progress, message }
GET  /download/<job_id> → video blob
GET  /health          → status JSON
"""

import os, re, uuid, json, base64, subprocess, shutil, tempfile, time, threading
from pathlib import Path
from flask import Flask, request, jsonify, send_file, make_response
from flask_cors import CORS
import requests as http_requests

app = Flask(__name__)

# ── CORS — explicit sab headers ───────────────────────────────────────────────
CORS(app, resources={r"/*": {
    "origins": "*",
    "allow_headers": ["Content-Type", "Authorization"],
    "methods": ["GET", "POST", "OPTIONS"],
    "supports_credentials": False,
    "max_age": 86400,
}})

app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024

# ── Paths ─────────────────────────────────────────────────────────────────────
WORK_DIR   = Path(tempfile.gettempdir()) / "dc_v2"
OUTPUT_DIR = WORK_DIR / "outputs"
for d in (WORK_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── In-memory jobs store ──────────────────────────────────────────────────────
jobs: dict = {}

# ── Gemini ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL   = "gemini-2.0-flash"
GEMINI_URL     = (f"https://generativelanguage.googleapis.com/v1beta"
                  f"/models/{GEMINI_MODEL}:generateContent")

# ── Quality Profiles ──────────────────────────────────────────────────────────
QUALITY_PROFILES = {
    "480p":  {"W": 854,  "H": 480,  "crf": 28, "preset": "ultrafast", "lookahead": 10},
    "720p":  {"W": 1280, "H": 720,  "crf": 25, "preset": "faster",    "lookahead": 20},
    "1080p": {"W": 1920, "H": 1080, "crf": 22, "preset": "medium",    "lookahead": 20},
}
DEFAULT_QUALITY = "480p"

MOTION_STYLES    = ["slow_push_in","slow_pull_back","drift_left","drift_right",
                    "dramatic_push","arc_left","arc_right","static_breathe"]
TRANSITION_TYPES = ["fade","dissolve","wipeleft","wiperight","circleopen"]
PACING_VALUES    = ["slow","moderate","dynamic","dramatic"]
ZOOM_INTENSITIES = ["low","medium","high"]


# ═══ CORS HELPER ═════════════════════════════════════════════════════════════

def _cors_headers(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

@app.after_request
def add_cors(response):
    return _cors_headers(response)

def options_response():
    """Standard 204 response for CORS preflight."""
    r = make_response("", 204)
    return _cors_headers(r)


# ═══ GEMINI AI DIRECTOR ═══════════════════════════════════════════════════════

def call_gemini(contents: list) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    payload = {"contents": contents,
               "generationConfig": {"maxOutputTokens": 4096,
                                    "responseMimeType": "application/json"}}
    for attempt in range(3):
        try:
            resp = http_requests.post(
                GEMINI_URL, params={"key": GEMINI_API_KEY},
                headers={"Content-Type": "application/json"},
                json=payload, timeout=90)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"Gemini failed: {e}")
            time.sleep(2 ** attempt)


def extract_gemini_text(resp: dict) -> str:
    try:
        return resp["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Cannot parse Gemini response: {e}")


def generate_fallback_plan(n: int, audio_duration, scene_duration: float) -> dict:
    per   = (audio_duration / n) if (audio_duration and audio_duration > 0) else scene_duration
    per   = max(3.0, per)
    arc   = ["opening","building","peak","falling","closing"]
    m_arc = {"opening":["slow_push_in","static_breathe"],
              "building":["drift_left","drift_right","arc_left"],
              "peak":["dramatic_push","arc_right","arc_left"],
              "falling":["slow_pull_back","drift_left"],
              "closing":["slow_pull_back","static_breathe"]}
    t_arc = {"opening":"fade","building":"dissolve",
              "peak":"wipeleft","falling":"dissolve","closing":"fade"}
    scenes = []
    for i in range(n):
        pos = arc[min(int(i / max(n-1,1) * (len(arc)-1)), len(arc)-1)]
        scenes.append({"scene_number":i+1,"duration":per,
                        "motion":m_arc[pos][i % len(m_arc[pos])],
                        "transition":t_arc[pos],"intensity":pos,
                        "direction_note":f"Scene {i+1} — {pos}"})
    return {"pacing":"moderate","mood":"cinematic","zoom_intensity":"medium",
            "transition_duration":1.2,"scenes":scenes}


def validate_plan(plan: dict, n: int, audio_duration) -> dict:
    scenes = plan.get("scenes", [])
    if len(scenes) < n:
        return generate_fallback_plan(n, audio_duration, 5.0)
    scenes = scenes[:n]
    for i, s in enumerate(scenes):
        if s.get("motion") not in MOTION_STYLES:
            s["motion"] = MOTION_STYLES[i % len(MOTION_STYLES)]
        if s.get("transition") not in TRANSITION_TYPES:
            s["transition"] = "fade"
        s["duration"]     = max(3.0, float(s.get("duration", 5.0)))
        s["scene_number"] = i + 1
    if audio_duration and audio_duration > 0:
        total = sum(s["duration"] for s in scenes)
        scenes = [{**s, "duration": max(3.0, s["duration"] * (audio_duration / total))} for s in scenes]
        diff = audio_duration - sum(s["duration"] for s in scenes)
        scenes[-1]["duration"] = max(3.0, scenes[-1]["duration"] + diff)
    plan["scenes"] = scenes
    if plan.get("pacing") not in PACING_VALUES:
        plan["pacing"] = "moderate"
    if plan.get("zoom_intensity") not in ZOOM_INTENSITIES:
        plan["zoom_intensity"] = "medium"
    td = float(plan.get("transition_duration", 1.2))
    plan["transition_duration"] = max(0.5, min(2.5, td))
    return plan


def ai_director_plan(scene_names: list, audio_duration, prompt: str,
                     scene_duration: float) -> dict:
    n = len(scene_names)
    if not GEMINI_API_KEY:
        return generate_fallback_plan(n, audio_duration, scene_duration)
    dur_info   = f"{audio_duration:.1f}s" if audio_duration else "not specified"
    scene_list = "\n".join([f"  {i+1}. {nm}" for i, nm in enumerate(scene_names)])
    user_hint  = f'\nUser style: "{prompt}"' if prompt else ""
    prompt_text = f"""You are an expert cinematic director. Create a per-scene cinematic plan.
Scenes ({n} total):
{scene_list}
Audio duration: {dur_info}
Default scene duration: {scene_duration}s{user_hint}

Return ONLY valid JSON:
{{"pacing":"moderate","mood":"cinematic","zoom_intensity":"medium","transition_duration":1.2,
  "scenes":[{{"scene_number":1,"duration":5.0,"motion":"slow_push_in","transition":"fade","intensity":"opening","direction_note":"brief"}}]}}

Allowed motion: slow_push_in,slow_pull_back,drift_left,drift_right,dramatic_push,arc_left,arc_right,static_breathe
Allowed transition: fade,dissolve,wipeleft,wiperight,circleopen
Allowed pacing: slow,moderate,dynamic,dramatic | zoom_intensity: low,medium,high
Rules: no consecutive same motion; min 3s/scene; durations sum to {dur_info} if audio given."""
    try:
        resp = call_gemini([{"role":"user","parts":[{"text":prompt_text}]}])
        plan = json.loads(extract_gemini_text(resp))
        return validate_plan(plan, n, audio_duration)
    except Exception as e:
        print(f"[AI Director] Gemini failed: {e}. Fallback.")
        return generate_fallback_plan(n, audio_duration, scene_duration)


# ═══ FFMPEG MOTION ENGINE ════════════════════════════════════════════════════

def get_zoom_filter(style: str, duration: float, zoom_intensity: str, quality: str) -> str:
    fps    = 25
    frames = max(int(duration * fps), 30)
    prof   = QUALITY_PROFILES.get(quality, QUALITY_PROFILES[DEFAULT_QUALITY])
    W, H   = prof["W"], prof["H"]
    SW, SH = W * 2, H * 2
    cx     = "iw/2-(iw/zoom/2)"
    cy     = "ih/2-(ih/zoom/2)"
    mult   = {"low":0.55,"medium":1.0,"high":1.5}.get(zoom_intensity, 1.0)
    slow_d = max(1, int(W * 0.22 / frames))
    fast_d = max(1, int(W * 0.30 / frames))
    base   = f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"

    if style == "slow_push_in":
        end = min(1.0+0.38*mult, 1.65); rate = (end-1.0)/frames
        zp  = f"zoompan=z='min(1.0+on*{rate:.5f},{end:.3f})':x='{cx}':y='{cy}':d={frames}:s={W}x{H}:fps={fps}"
    elif style == "slow_pull_back":
        start = min(1.0+0.38*mult, 1.65); rate = (start-1.0)/frames
        zp = (f"zoompan=z='if(eq(on\\,1)\\,{start:.3f}\\,max({start:.3f}-on*{rate:.5f}\\,1.0))':"
              f"x='{cx}':y='{cy}':d={frames}:s={W}x{H}:fps={fps}")
    elif style == "drift_left":
        zoom_val = 1.0+0.2*mult
        zp = (f"zoompan=z='{zoom_val:.3f}':"
              f"x='iw/2-(iw/zoom/2)+{slow_d}*on':y='{cy}':d={frames}:s={W}x{H}:fps={fps}")
    elif style == "drift_right":
        zoom_val = 1.0+0.2*mult
        zp = (f"zoompan=z='{zoom_val:.3f}':"
              f"x='iw/2-(iw/zoom/2)-{slow_d}*on':y='{cy}':d={frames}:s={W}x{H}:fps={fps}")
    elif style == "dramatic_push":
        end = min(1.0+0.55*mult, 1.80); rate = (end-1.0)/frames
        zp  = (f"zoompan=z='min(1.0+on*{rate:.5f},{end:.3f})':"
               f"x='iw/2-(iw/zoom/2)':y='ih*0.58-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={fps}")
    elif style == "arc_left":
        end = min(1.1+0.28*mult, 1.55); rate = (end-1.1)/frames
        zp  = (f"zoompan=z='min(1.1+on*{rate:.5f},{end:.3f})':"
               f"x='iw/2-(iw/zoom/2)+{fast_d}*on':y='{cy}':d={frames}:s={W}x{H}:fps={fps}")
    elif style == "arc_right":
        end = min(1.1+0.28*mult, 1.55); rate = (end-1.1)/frames
        zp  = (f"zoompan=z='min(1.1+on*{rate:.5f},{end:.3f})':"
               f"x='iw/2-(iw/zoom/2)-{fast_d}*on':y='{cy}':d={frames}:s={W}x{H}:fps={fps}")
    else:  # static_breathe
        end = 1.0+0.09*mult; rate = (end-1.0)/frames
        zp  = (f"zoompan=z='min(1.0+on*{rate:.5f},{end:.3f})':"
               f"x='{cx}':y='{cy}':d={frames}:s={W}x{H}:fps={fps}")
    return base + zp


def get_audio_duration(audio_path: str):
    try:
        r = subprocess.run(
            ["ffprobe","-v","error","-show_entries","format=duration",
             "-of","default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=15)
        return float(r.stdout.strip())
    except Exception:
        return None


def merge_clips_xfade(clip_paths: list, hold_times: list, scene_transitions: list,
                      transition_duration: float, output_path: str, quality: str):
    n = len(clip_paths)
    if n == 1:
        shutil.copy(clip_paths[0], output_path); return
    prof   = QUALITY_PROFILES.get(quality, QUALITY_PROFILES[DEFAULT_QUALITY])
    inputs = []
    for p in clip_paths:
        inputs += ["-i", p]
    parts, current, cumulative = [], "[0:v]", 0.0
    for i in range(1, n):
        cumulative += hold_times[i-1]
        out = f"[v{i}]" if i < n-1 else "[vout]"
        xft = scene_transitions[i-1] if i-1 < len(scene_transitions) else "fade"
        if xft not in TRANSITION_TYPES: xft = "fade"
        parts.append(f"{current}[{i}:v]xfade=transition={xft}"
                     f":duration={transition_duration:.3f}:offset={cumulative:.3f}{out}")
        current = out
    cmd = (["ffmpeg","-y","-threads","2"] + inputs
           + ["-filter_complex",";".join(parts),
              "-map","[vout]","-c:v","libx264","-pix_fmt","yuv420p",
              "-preset",prof["preset"],"-crf",str(prof["crf"]),
              "-x264-params",f"rc-lookahead={prof['lookahead']}:ref=1:threads=2",
              "-movflags","+faststart", output_path])
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        _, se = proc.communicate(timeout=900)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.communicate()
        raise RuntimeError("xfade merge timed out")
    if proc.returncode != 0:
        raise RuntimeError(f"xfade merge failed:\n{se[-1000:].decode('utf-8','replace')}")


# ═══ CORE VIDEO BUILDER ══════════════════════════════════════════════════════

def build_video(job_id: str, job_dir: Path, image_paths: list, audio_path,
                quality: str, prompt: str, scene_duration: float, output_path: Path):
    def upd(progress, message, status="processing"):
        jobs[job_id].update({"progress": progress, "message": message, "status": status})
        print(f"[{job_id[:8]}] {progress}% — {message}")

    try:
        upd(5, "AI Director reading scenes...")
        n             = len(image_paths)
        prof          = QUALITY_PROFILES.get(quality, QUALITY_PROFILES[DEFAULT_QUALITY])
        audio_duration = get_audio_duration(audio_path) if audio_path and os.path.exists(audio_path or "") else None

        upd(10, "Generating cinematic plan...")
        scene_names = [Path(p).name for p in image_paths]
        plan        = ai_director_plan(scene_names, audio_duration, prompt, scene_duration)

        zoom_intensity      = plan.get("zoom_intensity", "medium")
        transition_duration = float(plan.get("transition_duration", 1.2))
        scenes              = plan.get("scenes", [])

        upd(15, f"Plan ready — {plan.get('pacing')} pacing, {plan.get('mood')} mood")

        # Per-scene durations
        scene_durations = [max(3.0, float(scenes[i].get("duration", scene_duration))
                               if i < len(scenes) else scene_duration) for i in range(n)]
        if audio_duration and audio_duration > 0:
            total = sum(scene_durations)
            if abs(total - audio_duration) > 0.1:
                scene_durations = [max(3.0, d*(audio_duration/total)) for d in scene_durations]
            diff = audio_duration - sum(scene_durations)
            scene_durations[-1] = max(3.0, scene_durations[-1] + diff)

        hold_times     = [max(2.0, d - transition_duration) for d in scene_durations]
        clip_durations = [ht + transition_duration for ht in hold_times]

        # Audio sync buffer
        if audio_duration and audio_duration > 0:
            expected = sum(hold_times) + transition_duration
            if expected < audio_duration + 0.8:
                deficit = (audio_duration + 0.8) - expected
                hold_times[-1]     += deficit
                clip_durations[-1]  = hold_times[-1] + transition_duration

        # Render clips
        clip_paths = []
        for i, img_path in enumerate(image_paths):
            motion = (scenes[i].get("motion", MOTION_STYLES[i % len(MOTION_STYLES)])
                      if i < len(scenes) else MOTION_STYLES[i % len(MOTION_STYLES)])
            if motion not in MOTION_STYLES:
                motion = MOTION_STYLES[i % len(MOTION_STYLES)]
            clip_out = job_dir / f"clip_{i:03d}.mp4"
            vf       = get_zoom_filter(motion, clip_durations[i], zoom_intensity, quality)
            cmd = ["ffmpeg","-y","-loop","1","-framerate","25","-threads","1",
                   "-i", img_path, "-vf", vf, "-t", f"{clip_durations[i]:.3f}",
                   "-c:v","libx264","-pix_fmt","yuv420p","-r","25",
                   "-preset",prof["preset"],"-crf",str(prof["crf"]),
                   "-x264-params",f"rc-lookahead={prof['lookahead']}:ref=1:threads=1",
                   str(clip_out)]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                _, se = proc.communicate(timeout=240)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.communicate()
                raise RuntimeError(f"Clip {i+1} timed out")
            if proc.returncode != 0:
                raise RuntimeError(f"Clip {i+1} failed:\n{se[-800:].decode('utf-8','replace')}")
            clip_paths.append(str(clip_out))
            pct = 15 + int((i+1)/n * 60)
            upd(pct, f"Scene {i+1}/{n} — {motion.replace('_',' ')}")

        upd(77, "Weaving cinematic transitions...")
        raw_video = job_dir / "raw_video.mp4"
        merge_clips_xfade(clip_paths, hold_times,
                          [s.get("transition","fade") for s in scenes],
                          transition_duration, str(raw_video), quality)

        upd(90, "Syncing audio...")
        if audio_path and os.path.exists(audio_path):
            fade_st = max(0.0, (audio_duration or 0) - 1.0)
            for attempt_cmd in [
                ["ffmpeg","-y","-i",str(raw_video),"-i",audio_path,
                 "-c:v","copy","-c:a","aac","-b:a","192k",
                 "-af",f"afade=t=out:st={fade_st:.3f}:d=1.0",
                 "-shortest","-map","0:v:0","-map","1:a:0",str(output_path)],
                ["ffmpeg","-y","-i",str(raw_video),"-i",audio_path,
                 "-c:v","copy","-c:a","aac","-b:a","192k",
                 "-shortest","-map","0:v:0","-map","1:a:0",str(output_path)],
            ]:
                proc = subprocess.Popen(attempt_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                try:
                    _, se = proc.communicate(timeout=300)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.communicate(); continue
                if proc.returncode == 0:
                    break
            else:
                raise RuntimeError("Audio mix failed")
        else:
            shutil.copy(str(raw_video), str(output_path))

        size_kb = output_path.stat().st_size // 1024
        jobs[job_id].update({
            "status": "done",
            "progress": 100,
            "message": f"Director's cut complete — {size_kb} KB",
            "output_path": str(output_path),
        })
        print(f"[{job_id[:8]}] Done — {size_kb} KB")

    except Exception as e:
        import traceback; traceback.print_exc()
        jobs[job_id].update({"status": "error", "message": str(e), "progress": 0})
        try:
            shutil.rmtree(str(job_dir), ignore_errors=True)
        except Exception:
            pass


# ═══ FLASK ROUTES ════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    return jsonify({"status": "ok", "gemini": bool(GEMINI_API_KEY),
                    "default_quality": DEFAULT_QUALITY, "jobs": len(jobs)})


@app.route("/generate-video", methods=["POST", "OPTIONS"])
def generate_video():
    if request.method == "OPTIONS":
        return options_response()
    try:
        data           = request.get_json(force=True, silent=True) or {}
        images_b64     = data.get("images", [])
        audio_b64      = data.get("audio")
        quality        = data.get("quality", DEFAULT_QUALITY)
        prompt         = str(data.get("prompt", "")).strip()
        scene_duration = float(data.get("scene_duration", 5))

        if not images_b64:
            return jsonify({"error": "No images provided"}), 400
        if quality not in QUALITY_PROFILES:
            quality = DEFAULT_QUALITY

        job_id  = uuid.uuid4().hex
        job_dir = OUTPUT_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # Save images
        image_paths = []
        for i, b64_str in enumerate(images_b64):
            raw = b64_str.split(",",1)[-1] if "," in b64_str else b64_str
            ext = ".png" if "image/png" in b64_str[:30] else \
                  ".webp" if "image/webp" in b64_str[:30] else ".jpg"
            p = job_dir / f"scene_{i:03d}{ext}"
            p.write_bytes(base64.b64decode(raw))
            image_paths.append(str(p))

        def _sort_key(p):
            m = re.search(r"(\d+)", Path(p).stem)
            return int(m.group(1)) if m else 9999
        image_paths.sort(key=_sort_key)

        # Save audio
        audio_path = None
        if audio_b64:
            raw_audio = audio_b64.split(",",1)[-1] if "," in audio_b64 else audio_b64
            audio_ext = ".mp3"
            for mime, ext in [("audio/wav",".wav"),("audio/ogg",".ogg"),
                               ("audio/aac",".aac"),("audio/mp4",".m4a"),
                               ("audio/flac",".flac")]:
                if mime in audio_b64[:40]:
                    audio_ext = ext; break
            ap = job_dir / f"audio{audio_ext}"
            ap.write_bytes(base64.b64decode(raw_audio))
            audio_path = str(ap)

        output_path = job_dir / "output.mp4"

        # Init job
        jobs[job_id] = {
            "status": "queued", "progress": 0,
            "message": "Queued...", "output_path": None,
        }

        # Start background thread
        threading.Thread(
            target=build_video,
            args=(job_id, job_dir, image_paths, audio_path,
                  quality, prompt, scene_duration, output_path),
            daemon=True,
        ).start()

        return jsonify({"job_id": job_id})

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/status/<job_id>", methods=["GET", "OPTIONS"])
def job_status(job_id):
    if request.method == "OPTIONS":
        return options_response()
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    j = jobs[job_id]
    return jsonify({
        "status":   j["status"],
        "progress": j["progress"],
        "message":  j["message"],
    })


@app.route("/download/<job_id>", methods=["GET", "OPTIONS"])
def download_video(job_id):
    if request.method == "OPTIONS":
        return options_response()
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    j = jobs[job_id]
    if j["status"] != "done":
        return jsonify({"error": "Not ready", "status": j["status"]}), 400
    out = j.get("output_path")
    if not out or not os.path.exists(out):
        return jsonify({"error": "File missing"}), 404
    resp = send_file(out, mimetype="video/mp4", as_attachment=False,
                     download_name=f"directors_cut_{job_id[:8]}.mp4")
    # Cleanup after 5 min
    def _cleanup():
        time.sleep(300)
        job_dir = Path(out).parent
        shutil.rmtree(str(job_dir), ignore_errors=True)
        jobs.pop(job_id, None)
    threading.Thread(target=_cleanup, daemon=True).start()
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    app.run(host="0.0.0.0", port=port, debug=False)
