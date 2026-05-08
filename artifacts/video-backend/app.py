import os
import re
import uuid
import json
import base64
import subprocess
import threading
import shutil
import time
from pathlib import Path
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import requests as http_requests

app = Flask(__name__)
CORS(app, origins="*")

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
ALLOWED_AUDIO_EXTS = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}

jobs = {}

# ─── Gemini AI Director ────────────────────────────────────────────────────────

GEMINI_BASE_URL = os.environ.get("AI_INTEGRATIONS_GEMINI_BASE_URL", "").rstrip("/")
GEMINI_API_KEY = os.environ.get("AI_INTEGRATIONS_GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"

AUDIO_MIME_MAP = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}

MOTION_STYLES = [
    "slow_push_in",
    "slow_pull_back",
    "drift_left",
    "drift_right",
    "dramatic_push",
    "arc_left",
    "arc_right",
    "static_breathe",
]

TRANSITION_TYPES = ["fade", "dissolve", "wipeleft", "wiperight", "circleopen"]


def call_gemini(contents: list, response_json: bool = True) -> dict:
    url = f"{GEMINI_BASE_URL}/models/{GEMINI_MODEL}:generateContent"
    payload = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 8192,
        },
    }
    if response_json:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GEMINI_API_KEY}",
    }

    for attempt in range(3):
        try:
            resp = http_requests.post(url, headers=headers, json=payload, timeout=90)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"Gemini API call failed after 3 attempts: {e}")
            time.sleep(2 ** attempt)


def extract_gemini_text(gemini_response: dict) -> str:
    try:
        return gemini_response["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Could not extract Gemini response text: {e}")


def generate_ai_director_plan(audio_path: str | None, audio_duration: float | None,
                               scene_names: list[str]) -> dict:
    n = len(scene_names)

    if audio_path and os.path.exists(audio_path) and GEMINI_BASE_URL:
        # Check file size (8 MB limit for inline data)
        audio_size = os.path.getsize(audio_path)
        if audio_size > 8 * 1024 * 1024:
            return generate_fallback_plan(n, audio_duration)

        ext = Path(audio_path).suffix.lower()
        mime = AUDIO_MIME_MAP.get(ext, "audio/mpeg")

        with open(audio_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("utf-8")

        duration_info = f"{audio_duration:.1f} seconds" if audio_duration else "unknown duration"
        scene_list = "\n".join([f"  - {name}" for name in scene_names])

        prompt = f"""You are an expert cinematic director. Analyze this voiceover audio and create a precise cinematic direction plan for a {n}-scene video sequence.

The scenes are named (in order):
{scene_list}

Total audio duration: {duration_info}

Your task: analyze the emotional pacing, rhythm, and mood of the voiceover. Then create a per-scene cinematic direction plan.

Return ONLY valid JSON with this exact structure:
{{
  "pacing": "one of: slow, moderate, dynamic, dramatic",
  "mood": "brief mood descriptor e.g. 'contemplative', 'urgent', 'hopeful', 'melancholic'",
  "scenes": [
    {{
      "scene_number": 1,
      "duration": <float, seconds for this scene>,
      "motion": "<one of: slow_push_in, slow_pull_back, drift_left, drift_right, dramatic_push, arc_left, arc_right, static_breathe>",
      "transition": "<one of: fade, dissolve, wipeleft, wiperight, circleopen>",
      "intensity": "<one of: opening, building, peak, falling, closing>",
      "direction_note": "<brief cinematic note, max 8 words>"
    }}
  ]
}}

Rules:
- scene durations must sum to approximately {duration_info}
- vary motion styles — no two consecutive scenes should use identical motion
- match motion intensity to emotional arc of voiceover
- opening scenes: prefer slow_push_in or static_breathe
- peak/climax scenes: prefer dramatic_push or drift_left/right
- closing scenes: prefer slow_pull_back or static_breathe
- transitions: use fade or dissolve for emotional moments, wipes for energetic cuts
- minimum scene duration: 2.5 seconds
- maximum scene duration: 15 seconds"""

        contents = [
            {
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": mime, "data": audio_b64}},
                    {"text": prompt},
                ],
            }
        ]

        try:
            gemini_resp = call_gemini(contents, response_json=True)
            text = extract_gemini_text(gemini_resp)
            plan = json.loads(text)
            plan = validate_and_fix_plan(plan, n, audio_duration)
            return plan
        except Exception as e:
            print(f"[AI Director] Gemini analysis failed: {e}. Using fallback.")
            return generate_fallback_plan(n, audio_duration)
    else:
        # No audio or no Gemini — generate a smart default cinematic plan
        return generate_fallback_plan(n, audio_duration)


def validate_and_fix_plan(plan: dict, n: int, audio_duration: float | None) -> dict:
    scenes = plan.get("scenes", [])
    # Ensure we have exactly n scenes
    if len(scenes) < n:
        fallback = generate_fallback_plan(n, audio_duration)
        return fallback
    scenes = scenes[:n]

    # Fix any invalid motion/transition values
    for i, s in enumerate(scenes):
        if s.get("motion") not in MOTION_STYLES:
            s["motion"] = MOTION_STYLES[i % len(MOTION_STYLES)]
        if s.get("transition") not in TRANSITION_TYPES:
            s["transition"] = "fade"
        s["scene_number"] = i + 1
        s["duration"] = max(2.5, float(s.get("duration", 4.0)))

    # Re-balance durations to match audio
    if audio_duration and audio_duration > 0:
        total = sum(s["duration"] for s in scenes)
        scale = audio_duration / total
        for s in scenes:
            s["duration"] = max(2.5, s["duration"] * scale)

    plan["scenes"] = scenes
    return plan


def generate_fallback_plan(n: int, audio_duration: float | None) -> dict:
    """Generate a cinematically intelligent default plan without AI."""
    per_scene = (audio_duration / n) if (audio_duration and audio_duration > 0) else 5.0

    # Cinematic arc: opening → building → peak → falling → closing
    intensity_arc = ["opening", "building", "peak", "falling", "closing"]
    motion_arc = {
        "opening":  ["slow_push_in", "static_breathe"],
        "building": ["drift_left", "drift_right", "arc_left"],
        "peak":     ["dramatic_push", "drift_right", "arc_right"],
        "falling":  ["slow_pull_back", "drift_left", "arc_left"],
        "closing":  ["slow_pull_back", "static_breathe"],
    }
    transition_arc = {
        "opening":  "fade",
        "building": "dissolve",
        "peak":     "wipeleft",
        "falling":  "dissolve",
        "closing":  "fade",
    }

    scenes = []
    for i in range(n):
        # Map scene index to emotional arc position
        arc_pos = intensity_arc[min(int(i / max(n - 1, 1) * (len(intensity_arc) - 1)), len(intensity_arc) - 1)]
        motions = motion_arc[arc_pos]
        motion = motions[i % len(motions)]

        scenes.append({
            "scene_number": i + 1,
            "duration": per_scene,
            "motion": motion,
            "transition": transition_arc[arc_pos],
            "intensity": arc_pos,
            "direction_note": f"Scene {i+1} — {arc_pos}",
        })

    return {
        "pacing": "moderate",
        "mood": "cinematic",
        "scenes": scenes,
    }


# ─── Scene Ordering ────────────────────────────────────────────────────────────

def extract_scene_number(filename: str) -> tuple[int, str]:
    """Extract leading/embedded scene number for sorting."""
    stem = Path(filename).stem.lower()
    # Match "scene3", "scene_3", "3", "03", "img3" etc.
    m = re.search(r"(\d+)", stem)
    num = int(m.group(1)) if m else 9999
    return (num, filename)


def sort_images_by_scene(image_paths: list[str]) -> list[str]:
    """Sort image paths by scene number embedded in filename."""
    def key(p):
        return extract_scene_number(Path(p).name)
    return sorted(image_paths, key=key)


# ─── File utilities ────────────────────────────────────────────────────────────

def allowed_file(filename, allowed_exts):
    return Path(filename).suffix.lower() in allowed_exts


def get_audio_duration(audio_path: str) -> float | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=15,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


# ─── FFmpeg Motion Engine ──────────────────────────────────────────────────────

def get_zoom_filter(style: str, duration: float) -> str:
    fps = 25
    frames = max(int(duration * fps), 25)
    W, H = 1920, 1080

    # Scale factor for zoompan source — 2× overscale for smooth motion
    SW, SH = W * 2, H * 2

    # Center anchor expressions
    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"

    # Movement magnitude per frame (slow = cinematic)
    slow_drift = int(W * 0.25 / frames)   # pan distance spread across clip
    fast_drift = int(W * 0.35 / frames)

    if style == "slow_push_in":
        # Slow, dramatic zoom in from 1.0 → 1.45 centered
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='min(1.0+on*0.45/{frames},1.45)':"
              f"x='{cx}':y='{cy}':d={frames}:s={W}x{H}:fps={fps}")

    elif style == "slow_pull_back":
        # Start zoomed in, slowly pull back to wide
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='if(eq(on\\,1)\\,1.45\\,max(1.45-on*0.45/{frames}\\,1.0))':"
              f"x='{cx}':y='{cy}':d={frames}:s={W}x{H}:fps={fps}")

    elif style == "drift_left":
        # Pan left with gentle zoom
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='1.25':"
              f"x='iw/2-(iw/zoom/2)+{slow_drift}*on':y='{cy}':"
              f"d={frames}:s={W}x{H}:fps={fps}")

    elif style == "drift_right":
        # Pan right with gentle zoom
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='1.25':"
              f"x='iw/2-(iw/zoom/2)-{slow_drift}*on':y='{cy}':"
              f"d={frames}:s={W}x{H}:fps={fps}")

    elif style == "dramatic_push":
        # Aggressive zoom into lower-third for climactic moments
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='min(1.0+on*0.6/{frames},1.6)':"
              f"x='iw/2-(iw/zoom/2)':y='ih*0.6-(ih/zoom/2)':"
              f"d={frames}:s={W}x{H}:fps={fps}")

    elif style == "arc_left":
        # Slow pan left + simultaneous push in (arc camera move)
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='min(1.1+on*0.3/{frames},1.4)':"
              f"x='iw/2-(iw/zoom/2)+{fast_drift}*on':y='{cy}':"
              f"d={frames}:s={W}x{H}:fps={fps}")

    elif style == "arc_right":
        # Slow pan right + simultaneous push in
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='min(1.1+on*0.3/{frames},1.4)':"
              f"x='iw/2-(iw/zoom/2)-{fast_drift}*on':y='{cy}':"
              f"d={frames}:s={W}x{H}:fps={fps}")

    else:  # static_breathe — almost imperceptible zoom, meditative
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='min(1.0+on*0.12/{frames},1.12)':"
              f"x='{cx}':y='{cy}':d={frames}:s={W}x{H}:fps={fps}")

    return zp


# ─── FFmpeg Merge with Xfade ───────────────────────────────────────────────────

XFADE_TRANSITIONS = {
    "fade": "fade",
    "dissolve": "dissolve",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    "circleopen": "circleopen",
}


def merge_clips_cinematic(clip_paths: list[str], hold_times: list[float],
                           transition_duration: float, output_path: str):
    n = len(clip_paths)
    if n == 1:
        shutil.copy(clip_paths[0], output_path)
        return

    inputs = []
    for p in clip_paths:
        inputs += ["-i", p]

    # Build xfade filter chain
    # xfade offset = cumulative sum of hold_times up to that transition point
    filter_parts = []
    current = "[0:v]"
    cumulative_offset = 0.0

    for i in range(1, n):
        cumulative_offset += hold_times[i - 1]
        next_label = f"[v{i}]" if i < n - 1 else "[vout]"
        xf_type = "fade"  # default; scene-level transition type stored in job metadata
        filter_parts.append(
            f"{current}[{i}:v]xfade=transition={xf_type}"
            f":duration={transition_duration}:offset={cumulative_offset:.3f}{next_label}"
        )
        current = next_label

    filter_complex = ";".join(filter_parts)
    final_label = "[vout]" if n > 1 else current

    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + [
            "-filter_complex", filter_complex,
            "-map", final_label,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-crf", "18",
            output_path,
        ]
    )
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise RuntimeError(f"Cinematic merge failed:\n{result.stderr[-1000:]}")


def merge_clips_with_scene_transitions(clip_paths: list[str], hold_times: list[float],
                                        scene_transitions: list[str],
                                        transition_duration: float, output_path: str):
    """Merge clips using per-scene transition types from the AI director plan."""
    n = len(clip_paths)
    if n == 1:
        shutil.copy(clip_paths[0], output_path)
        return

    inputs = []
    for p in clip_paths:
        inputs += ["-i", p]

    filter_parts = []
    current = "[0:v]"
    cumulative_offset = 0.0

    for i in range(1, n):
        cumulative_offset += hold_times[i - 1]
        next_label = f"[v{i}]" if i < n - 1 else "[vout]"
        # Use the transition type from the PREVIOUS scene (transition out of scene i-1)
        raw_type = scene_transitions[i - 1] if i - 1 < len(scene_transitions) else "fade"
        xf_type = XFADE_TRANSITIONS.get(raw_type, "fade")
        filter_parts.append(
            f"{current}[{i}:v]xfade=transition={xf_type}"
            f":duration={transition_duration:.3f}:offset={cumulative_offset:.3f}{next_label}"
        )
        current = next_label

    filter_complex = ";".join(filter_parts)
    final_label = "[vout]"

    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + [
            "-filter_complex", filter_complex,
            "-map", final_label,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-crf", "18",
            output_path,
        ]
    )
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise RuntimeError(f"Cinematic merge failed:\n{result.stderr[-1000:]}")


# ─── Core Video Builder ────────────────────────────────────────────────────────

def build_cinematic_video(job_id: str, image_paths: list[str], audio_path: str | None,
                           options: dict):
    try:
        jobs[job_id]["status"] = "analyzing"
        jobs[job_id]["progress"] = 3
        jobs[job_id]["message"] = "AI Director is reading the voiceover..."

        n = len(image_paths)
        audio_duration = options.get("audio_duration")
        transition_duration = float(options.get("transition_duration", 1.2))
        per_image_fallback = float(options.get("per_image_duration", 5.0))
        ai_plan_override = options.get("ai_plan")  # optional pre-computed plan

        # Get scene names for context
        scene_names = [Path(p).name for p in image_paths]

        # Step 1: Get AI Director plan
        if ai_plan_override:
            plan = ai_plan_override
        else:
            plan = generate_ai_director_plan(audio_path, audio_duration, scene_names)

        jobs[job_id]["ai_plan"] = plan
        jobs[job_id]["progress"] = 12
        jobs[job_id]["message"] = f"AI Director: {plan.get('mood', 'cinematic')} pacing locked in. Building sequences..."

        scenes = plan.get("scenes", [])

        # Resolve per-scene durations
        scene_durations = []
        for i in range(n):
            if i < len(scenes):
                dur = float(scenes[i].get("duration", per_image_fallback))
            else:
                dur = per_image_fallback
            scene_durations.append(max(2.5, dur))

        # If audio, normalize total duration
        if audio_duration and audio_duration > 0:
            total = sum(scene_durations)
            if abs(total - audio_duration) > 0.5:
                scale = audio_duration / total
                scene_durations = [max(2.5, d * scale) for d in scene_durations]

        # Hold time = scene duration (xfade overlap adds extra time)
        hold_times = [max(1.5, d - transition_duration) for d in scene_durations]

        # Clip total duration = hold_time + transition overlap
        clip_durations = [ht + transition_duration for ht in hold_times]

        jobs[job_id]["progress"] = 18
        jobs[job_id]["message"] = "Rendering cinematic clips..."

        clip_paths = []
        temp_dir = OUTPUT_DIR / f"tmp_{job_id}"
        temp_dir.mkdir(exist_ok=True)

        for i, img_path in enumerate(image_paths):
            clip_out = temp_dir / f"clip_{i:03d}.mp4"
            # Get motion style from AI plan
            if i < len(scenes):
                motion = scenes[i].get("motion", "slow_push_in")
                if motion not in MOTION_STYLES:
                    motion = MOTION_STYLES[i % len(MOTION_STYLES)]
            else:
                motion = MOTION_STYLES[i % len(MOTION_STYLES)]

            clip_duration = clip_durations[i]
            vf = get_zoom_filter(motion, clip_duration)

            cmd = [
                "ffmpeg", "-y",
                "-loop", "1",
                "-framerate", "25",
                "-i", img_path,
                "-vf", vf,
                "-t", f"{clip_duration:.3f}",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-r", "25",
                "-preset", "fast",
                "-crf", "18",
                str(clip_out),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if result.returncode != 0:
                raise RuntimeError(f"Clip {i+1} render failed:\n{result.stderr[-600:]}")

            clip_paths.append(str(clip_out))
            pct = 18 + int((i + 1) / n * 52)
            motion_label = motion.replace("_", " ").upper()
            jobs[job_id]["progress"] = pct
            jobs[job_id]["message"] = f"Scene {i+1}/{n} — {motion_label}"

        jobs[job_id]["progress"] = 72
        jobs[job_id]["message"] = "Weaving cinematic transitions..."

        # Merge with per-scene transition types
        scene_transitions = [s.get("transition", "fade") for s in scenes]
        raw_video = temp_dir / "raw_video.mp4"
        merge_clips_with_scene_transitions(
            clip_paths, hold_times, scene_transitions, transition_duration, str(raw_video)
        )

        jobs[job_id]["progress"] = 88
        jobs[job_id]["message"] = "Laying down the voiceover..."

        output_path = OUTPUT_DIR / f"{job_id}.mp4"

        if audio_path and os.path.exists(audio_path):
            mix_cmd = [
                "ffmpeg", "-y",
                "-i", str(raw_video),
                "-i", audio_path,
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                "-map", "0:v:0",
                "-map", "1:a:0",
                str(output_path),
            ]
            result = subprocess.run(mix_cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                raise RuntimeError(f"Audio mix failed:\n{result.stderr[-600:]}")
        else:
            shutil.copy(str(raw_video), str(output_path))

        jobs[job_id]["progress"] = 100
        jobs[job_id]["status"] = "done"
        jobs[job_id]["message"] = "Director's cut complete."
        jobs[job_id]["output_path"] = str(output_path)
        jobs[job_id]["filename"] = f"{job_id}.mp4"

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["message"] = str(e)


# ─── Routes ────────────────────────────────────────────────────────────────────

@app.route("/video-api/health")
def health():
    return jsonify({"status": "ok", "gemini": bool(GEMINI_BASE_URL)})


@app.route("/video-api/upload/images", methods=["POST"])
def upload_images():
    if "images" not in request.files:
        return jsonify({"error": "No images provided"}), 400

    files = request.files.getlist("images")
    if not files:
        return jsonify({"error": "No images provided"}), 400

    session_id = str(uuid.uuid4())
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    original_names = []

    for f in files:
        if not f.filename:
            continue
        original_name = f.filename
        filename = secure_filename(original_name)
        ext = Path(filename).suffix.lower()
        if not allowed_file(filename, ALLOWED_IMAGE_EXTS):
            return jsonify({"error": f"Invalid image type: {ext}"}), 400
        dest = session_dir / filename
        # Avoid collisions
        if dest.exists():
            dest = session_dir / f"_{uuid.uuid4().hex[:6]}_{filename}"
        f.save(str(dest))
        saved.append(str(dest))
        original_names.append(original_name)

    if not saved:
        return jsonify({"error": "No valid images uploaded"}), 400

    # Sort by scene number in filename
    sorted_paths = sort_images_by_scene(saved)

    # Write metadata
    meta = {
        "original_names": original_names,
        "sorted_paths": sorted_paths,
    }
    with open(session_dir / "meta.json", "w") as f:
        json.dump(meta, f)

    return jsonify({
        "session_id": session_id,
        "count": len(sorted_paths),
        "paths": sorted_paths,
        "scene_names": [Path(p).name for p in sorted_paths],
    })


@app.route("/video-api/upload/audio", methods=["POST"])
def upload_audio():
    if "audio" not in request.files:
        return jsonify({"error": "No audio provided"}), 400

    f = request.files["audio"]
    if not f.filename:
        return jsonify({"error": "No audio filename"}), 400

    filename = secure_filename(f.filename)
    ext = Path(filename).suffix.lower()
    if not allowed_file(filename, ALLOWED_AUDIO_EXTS):
        return jsonify({"error": f"Invalid audio type: {ext}"}), 400

    audio_id = str(uuid.uuid4())
    audio_path = UPLOAD_DIR / f"audio_{audio_id}{ext}"
    f.save(str(audio_path))

    duration = get_audio_duration(str(audio_path))
    return jsonify({"audio_id": audio_id, "path": str(audio_path), "duration": duration})


@app.route("/video-api/analyze", methods=["POST"])
def analyze_route():
    """Pre-render AI analysis endpoint — lets the frontend display the plan before rendering."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    session_id = data.get("session_id")
    audio_path = data.get("audio_path")
    audio_duration = data.get("audio_duration")

    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    session_dir = UPLOAD_DIR / session_id
    if not session_dir.exists():
        return jsonify({"error": "Session not found"}), 404

    image_paths = sort_images_by_scene([
        str(p) for p in session_dir.iterdir()
        if p.suffix.lower() in ALLOWED_IMAGE_EXTS
    ])

    if not image_paths:
        return jsonify({"error": "No images in session"}), 400

    scene_names = [Path(p).name for p in image_paths]

    try:
        plan = generate_ai_director_plan(audio_path, audio_duration, scene_names)
        return jsonify({"plan": plan, "scene_names": scene_names})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/video-api/generate", methods=["POST"])
def generate_video():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    session_id = data.get("session_id")
    audio_path = data.get("audio_path")
    audio_duration = data.get("audio_duration")
    transition_duration = data.get("transition_duration", 1.2)
    per_image_duration = data.get("per_image_duration", 5.0)
    ai_plan = data.get("ai_plan")  # optional pre-computed plan from /analyze

    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    session_dir = UPLOAD_DIR / session_id
    if not session_dir.exists():
        return jsonify({"error": "Session not found"}), 404

    image_paths = sort_images_by_scene([
        str(p) for p in session_dir.iterdir()
        if p.suffix.lower() in ALLOWED_IMAGE_EXTS
    ])

    if not image_paths:
        return jsonify({"error": "No images found in session"}), 400

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "queued",
        "progress": 0,
        "message": "Queued for AI direction...",
        "output_path": None,
        "filename": None,
        "ai_plan": None,
    }

    options = {
        "audio_duration": audio_duration,
        "transition_duration": float(transition_duration),
        "per_image_duration": float(per_image_duration),
        "ai_plan": ai_plan,
    }

    thread = threading.Thread(
        target=build_cinematic_video,
        args=(job_id, image_paths, audio_path, options),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/video-api/status/<job_id>")
def job_status(job_id):
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    job = jobs[job_id]
    return jsonify({
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "filename": job.get("filename"),
        "ai_plan": job.get("ai_plan"),
    })


@app.route("/video-api/download/<job_id>")
def download_video(job_id):
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    job = jobs[job_id]
    if job["status"] != "done":
        return jsonify({"error": "Video not ready"}), 400
    output_path = job["output_path"]
    if not output_path or not os.path.exists(output_path):
        return jsonify({"error": "Output file missing"}), 404
    return send_file(
        output_path,
        as_attachment=True,
        download_name=f"directors_cut_{job_id[:8]}.mp4",
        mimetype="video/mp4",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
