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
ZOOM_INTENSITIES = ["low", "medium", "high"]

# ─── Quality Profiles ──────────────────────────────────────────────────────────
# Each profile defines output resolution, encoder settings, and RAM usage.
# DEFAULT_QUALITY can be overridden per-request via the "quality" param.
QUALITY_PROFILES = {
    "480p":  {"W": 854,  "H": 480,  "crf": 24, "preset": "faster", "lookahead": 10},
    "720p":  {"W": 1280, "H": 720,  "crf": 22, "preset": "medium", "lookahead": 20},
    "1080p": {"W": 1920, "H": 1080, "crf": 20, "preset": "medium", "lookahead": 20},
}
DEFAULT_QUALITY = os.environ.get("RENDER_QUALITY", "720p")
if DEFAULT_QUALITY not in QUALITY_PROFILES:
    DEFAULT_QUALITY = "720p"
PACING_VALUES = ["slow", "moderate", "dynamic", "dramatic"]


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
  "zoom_intensity": "one of: low, medium, high",
  "transition_duration": <float between 0.8 and 2.0>,
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
- scene durations MUST sum to exactly {duration_info}
- vary motion styles — no two consecutive scenes should use identical motion
- match motion intensity to emotional arc of voiceover
- opening scenes: prefer slow_push_in or static_breathe
- peak/climax scenes: prefer dramatic_push or arc_left/arc_right
- closing scenes: prefer slow_pull_back or static_breathe
- transitions: use fade or dissolve for emotional moments, wipes for energetic cuts
- minimum scene duration: 3.0 seconds
- maximum scene duration: 18 seconds
- zoom_intensity: low for subtle/emotional content, medium for general, high for dramatic/action
- transition_duration: 0.8-1.2 for fast pacing, 1.2-2.0 for slow/emotional"""

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
        return generate_fallback_plan(n, audio_duration)


def validate_and_fix_plan(plan: dict, n: int, audio_duration: float | None) -> dict:
    scenes = plan.get("scenes", [])
    if len(scenes) < n:
        fallback = generate_fallback_plan(n, audio_duration)
        return fallback
    scenes = scenes[:n]

    for i, s in enumerate(scenes):
        if s.get("motion") not in MOTION_STYLES:
            s["motion"] = MOTION_STYLES[i % len(MOTION_STYLES)]
        if s.get("transition") not in TRANSITION_TYPES:
            s["transition"] = "fade"
        if s.get("intensity") not in ["opening", "building", "peak", "falling", "closing"]:
            s["intensity"] = ["opening", "building", "peak", "falling", "closing"][i % 5]
        s["scene_number"] = i + 1
        s["duration"] = max(3.0, float(s.get("duration", 5.0)))

    # Re-balance durations to match audio precisely
    if audio_duration and audio_duration > 0:
        total = sum(s["duration"] for s in scenes)
        scale = audio_duration / total
        for s in scenes:
            s["duration"] = max(3.0, s["duration"] * scale)
        # Final adjustment to ensure exact sum
        total_after = sum(s["duration"] for s in scenes)
        diff = audio_duration - total_after
        scenes[-1]["duration"] = max(3.0, scenes[-1]["duration"] + diff)

    plan["scenes"] = scenes

    # Validate top-level fields
    if plan.get("pacing") not in PACING_VALUES:
        plan["pacing"] = "moderate"
    if not isinstance(plan.get("mood"), str) or not plan["mood"]:
        plan["mood"] = "cinematic"
    if plan.get("zoom_intensity") not in ZOOM_INTENSITIES:
        plan["zoom_intensity"] = "medium"
    td = float(plan.get("transition_duration", 1.2))
    plan["transition_duration"] = max(0.5, min(2.5, td))

    return plan


def generate_fallback_plan(n: int, audio_duration: float | None) -> dict:
    """Generate a cinematically intelligent default plan without AI."""
    per_scene = (audio_duration / n) if (audio_duration and audio_duration > 0) else 5.0

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
        arc_idx = min(int(i / max(n - 1, 1) * (len(intensity_arc) - 1)), len(intensity_arc) - 1)
        arc_pos = intensity_arc[arc_idx]
        motions = motion_arc[arc_pos]
        motion = motions[i % len(motions)]

        scenes.append({
            "scene_number": i + 1,
            "duration": per_scene,
            "motion": motion,
            "transition": transition_arc[arc_pos],
            "intensity": arc_pos,
            "direction_note": f"Scene {i + 1} — {arc_pos}",
        })

    return {
        "pacing": "moderate",
        "mood": "cinematic",
        "zoom_intensity": "medium",
        "transition_duration": 1.2,
        "scenes": scenes,
    }


# ─── AI Chat Director ──────────────────────────────────────────────────────────

def apply_chat_directive(message: str, current_plan: dict, scene_names: list[str],
                          audio_duration: float | None) -> dict:
    """Interpret a natural language directive and update the cinematic plan safely."""
    if not GEMINI_BASE_URL:
        return current_plan

    n = len(current_plan.get("scenes", []))
    if n == 0:
        return current_plan

    duration_info = f"{audio_duration:.1f} seconds" if audio_duration else "not specified"
    scene_list = "\n".join([f"  {i+1}. {name}" for i, name in enumerate(scene_names[:n])])

    prompt = f"""You are an AI cinematic director assistant. Apply the user's instruction to intelligently update the cinematic plan.

CURRENT PLAN:
{json.dumps(current_plan, indent=2)}

SCENES:
{scene_list}

AUDIO DURATION: {duration_info}

USER INSTRUCTION: "{message}"

Interpret the instruction and return a COMPLETE UPDATED PLAN as valid JSON with the exact same structure.

ALLOWED VALUES (strictly enforced — never use other values):
- motion: slow_push_in, slow_pull_back, drift_left, drift_right, dramatic_push, arc_left, arc_right, static_breathe
- transition: fade, dissolve, wipeleft, wiperight, circleopen
- pacing: slow, moderate, dynamic, dramatic
- zoom_intensity: low, medium, high
- intensity: opening, building, peak, falling, closing
- transition_duration: 0.5 to 2.5 (float)
- duration: minimum 3.0 seconds per scene

INTERPRETATION GUIDE:
- "emotional / melancholic / sad / reflective" → slow_push_in, static_breathe, slow_pull_back; pacing: slow; zoom_intensity: low; transitions: dissolve, fade
- "dramatic / intense / powerful / cinematic" → dramatic_push, arc_left, arc_right; pacing: dramatic; zoom_intensity: high; transitions: wipeleft, wiperight
- "subtle / gentle / soft / calm" → static_breathe, slow_push_in; zoom_intensity: low; pacing: slow
- "dynamic / energetic / fast / upbeat" → drift_left, drift_right, arc_left, arc_right; pacing: dynamic; zoom_intensity: medium
- "smoother transitions" → transition_duration: 1.8-2.2; transitions: dissolve, fade throughout
- "longer final scene / extend ending" → increase last 1-2 scene durations significantly, reduce earlier scenes
- "build to climax" → escalate from static_breathe → slow_push_in → drift → arc → dramatic_push
- "add dramatic zooms" → zoom_intensity: high; use dramatic_push and arc_left/arc_right at peak moments
- "more cinematic flow" → vary motions systematically, use dissolve transitions, pacing: moderate
- "faster pacing" → reduce scene durations, pacing: dynamic, transitions: wipeleft/wiperight
- "slower pacing" → increase scene durations, pacing: slow, transitions: dissolve/fade

CONSTRAINTS:
- If audio_duration is specified, total scene durations MUST sum to exactly {audio_duration:.1f if audio_duration else 'N/A'} seconds
- Keep scenes in logical cinematic order (opening → building → peak → falling → closing arc)
- Never use the same motion for two consecutive scenes

Return ONLY the complete JSON plan, no explanation or markdown."""

    contents = [{"role": "user", "parts": [{"text": prompt}]}]

    try:
        gemini_resp = call_gemini(contents, response_json=True)
        text = extract_gemini_text(gemini_resp)
        updated_plan = json.loads(text)
        updated_plan = validate_and_fix_plan(updated_plan, n, audio_duration)
        return updated_plan
    except Exception as e:
        print(f"[Chat Director] Failed to apply directive '{message}': {e}")
        return current_plan


# ─── Scene Ordering ────────────────────────────────────────────────────────────

def extract_scene_number(filename: str) -> tuple[int, str]:
    stem = Path(filename).stem.lower()
    m = re.search(r"(\d+)", stem)
    num = int(m.group(1)) if m else 9999
    return (num, filename)


def sort_images_by_scene(image_paths: list[str]) -> list[str]:
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

def get_zoom_filter(style: str, duration: float, zoom_intensity: str = "medium",
                    quality: str = "720p") -> str:
    fps = 25
    frames = max(int(duration * fps), 30)  # minimum 30 frames for smooth motion
    prof = QUALITY_PROFILES.get(quality, QUALITY_PROFILES["720p"])
    W, H = prof["W"], prof["H"]

    # Scale factor for zoompan source — 2× overscale prevents edge artifacts
    SW, SH = W * 2, H * 2

    # Center anchor expressions
    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"

    # Zoom intensity multipliers — controls the range of motion
    intensity_mult = {"low": 0.55, "medium": 1.0, "high": 1.5}
    mult = intensity_mult.get(zoom_intensity, 1.0)

    # Drift speed — proportional to frame count for smooth continuous motion
    slow_drift = int(W * 0.22 / frames)
    fast_drift = int(W * 0.30 / frames)
    # Ensure minimum drift of 1 pixel/frame for visible motion
    slow_drift = max(1, slow_drift)
    fast_drift = max(1, fast_drift)

    if style == "slow_push_in":
        push_end = min(1.0 + 0.38 * mult, 1.65)
        push_rate = (push_end - 1.0) / frames
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='min(1.0+on*{push_rate:.5f},{push_end:.3f})':"
              f"x='{cx}':y='{cy}':d={frames}:s={W}x{H}:fps={fps}")

    elif style == "slow_pull_back":
        pull_start = min(1.0 + 0.38 * mult, 1.65)
        pull_rate = (pull_start - 1.0) / frames
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='if(eq(on\\,1)\\,{pull_start:.3f}\\,max({pull_start:.3f}-on*{pull_rate:.5f}\\,1.0))':"
              f"x='{cx}':y='{cy}':d={frames}:s={W}x{H}:fps={fps}")

    elif style == "drift_left":
        zoom_val = 1.0 + 0.2 * mult
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='{zoom_val:.3f}':"
              f"x='iw/2-(iw/zoom/2)+{slow_drift}*on':y='{cy}':"
              f"d={frames}:s={W}x{H}:fps={fps}")

    elif style == "drift_right":
        zoom_val = 1.0 + 0.2 * mult
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='{zoom_val:.3f}':"
              f"x='iw/2-(iw/zoom/2)-{slow_drift}*on':y='{cy}':"
              f"d={frames}:s={W}x{H}:fps={fps}")

    elif style == "dramatic_push":
        push_end = min(1.0 + 0.55 * mult, 1.80)
        push_rate = (push_end - 1.0) / frames
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='min(1.0+on*{push_rate:.5f},{push_end:.3f})':"
              f"x='iw/2-(iw/zoom/2)':y='ih*0.58-(ih/zoom/2)':"
              f"d={frames}:s={W}x{H}:fps={fps}")

    elif style == "arc_left":
        arc_end = min(1.1 + 0.28 * mult, 1.55)
        arc_rate = (arc_end - 1.1) / frames
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='min(1.1+on*{arc_rate:.5f},{arc_end:.3f})':"
              f"x='iw/2-(iw/zoom/2)+{fast_drift}*on':y='{cy}':"
              f"d={frames}:s={W}x{H}:fps={fps}")

    elif style == "arc_right":
        arc_end = min(1.1 + 0.28 * mult, 1.55)
        arc_rate = (arc_end - 1.1) / frames
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='min(1.1+on*{arc_rate:.5f},{arc_end:.3f})':"
              f"x='iw/2-(iw/zoom/2)-{fast_drift}*on':y='{cy}':"
              f"d={frames}:s={W}x{H}:fps={fps}")

    else:  # static_breathe — barely perceptible, meditative
        breathe_end = 1.0 + 0.09 * mult
        breathe_rate = (breathe_end - 1.0) / frames
        zp = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"zoompan=z='min(1.0+on*{breathe_rate:.5f},{breathe_end:.3f})':"
              f"x='{cx}':y='{cy}':d={frames}:s={W}x{H}:fps={fps}")

    return zp


# ─── FFmpeg Merge with Per-Scene Xfade Transitions ────────────────────────────

XFADE_TRANSITIONS = {
    "fade": "fade",
    "dissolve": "dissolve",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    "circleopen": "circleopen",
}


def merge_clips_with_scene_transitions(clip_paths: list[str], hold_times: list[float],
                                        scene_transitions: list[str],
                                        transition_duration: float, output_path: str,
                                        quality: str = "720p"):
    """Merge N clips sequentially (2 at a time) with correct xfade offsets.

    Key fix: in sequential merging the LEFT input grows after each step.
    After merging clips 0..k into an accumulated clip, its duration is:
        sum(hold_times[0..k]) + transition_duration
    So the xfade offset for the NEXT merge must be that accumulated duration,
    NOT just hold_times[i-1] (which was the original bug causing only 2 scenes).

    Approach: use ffprobe to read the actual duration of the accumulated clip
    before each merge step — this is 100% accurate regardless of float drift.
    """
    n = len(clip_paths)
    if n == 1:
        shutil.copy(clip_paths[0], output_path)
        return

    _prof = QUALITY_PROFILES.get(quality, QUALITY_PROFILES["720p"])
    tmp_dir = Path(output_path).parent
    intermediates: list[str] = []
    current_clip = clip_paths[0]

    def get_duration(path: str) -> float:
        """Use ffprobe to get exact duration of a video file."""
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip())

    for i in range(1, n):
        is_last = (i == n - 1)
        next_clip = clip_paths[i]

        # Correct offset: actual duration of the accumulated left clip
        # minus the transition overlap (xfade offset = where transition starts)
        left_duration = get_duration(current_clip)
        offset = max(0.1, left_duration - transition_duration)

        raw_type = scene_transitions[i - 1] if i - 1 < len(scene_transitions) else "fade"
        xf_type = XFADE_TRANSITIONS.get(raw_type, "fade")

        if is_last:
            merge_out = output_path
        else:
            merge_out = str(tmp_dir / f"_seq_merge_{i:03d}.mp4")
            intermediates.append(merge_out)

        filter_complex = (
            f"[0:v][1:v]xfade=transition={xf_type}"
            f":duration={transition_duration:.3f}:offset={offset:.3f}[vout]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-threads", "1",
            "-i", current_clip,
            "-i", next_clip,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", _prof["preset"],
            "-crf", str(_prof["crf"]),
            "-x264-params", f"rc-lookahead={_prof['lookahead']}:ref=1:threads=1",
            "-movflags", "+faststart",
            merge_out,
        ]

        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            _, stderr_bytes = proc.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise RuntimeError(f"Merge step {i} timed out")

        if proc.returncode != 0:
            stderr_tail = stderr_bytes[-1500:].decode("utf-8", errors="replace")
            raise RuntimeError(f"Cinematic merge step {i} failed:\n{stderr_tail}")

        # Delete previous intermediate immediately to free disk space
        if current_clip in intermediates:
            try:
                os.remove(current_clip)
                intermediates.remove(current_clip)
            except OSError:
                pass

        current_clip = merge_out

    # Safety net cleanup
    for f in intermediates:
        try:
            os.remove(f)
        except OSError:
            pass

# ─── Core Video Builder ────────────────────────────────────────────────────────

def build_cinematic_video(job_id: str, image_paths: list[str], audio_path: str | None,
                           options: dict):
    try:
        jobs[job_id]["status"] = "analyzing"
        jobs[job_id]["progress"] = 3
        jobs[job_id]["message"] = "AI Director is reading the project..."

        n = len(image_paths)
        audio_duration = options.get("audio_duration")
        per_image_fallback = float(options.get("per_image_duration", 5.0))
        ai_plan_override = options.get("ai_plan")

        scene_names = [Path(p).name for p in image_paths]

        # Step 1: Get AI Director plan
        if ai_plan_override:
            plan = ai_plan_override
            # Re-validate to ensure all fields are safe
            plan = validate_and_fix_plan(plan, n, audio_duration)
        else:
            plan = generate_ai_director_plan(audio_path, audio_duration, scene_names)

        jobs[job_id]["ai_plan"] = plan
        jobs[job_id]["progress"] = 12
        zoom_intensity = plan.get("zoom_intensity", "medium")
        transition_duration = float(plan.get("transition_duration", 1.2))
        quality = options.get("quality", DEFAULT_QUALITY)
        if quality not in QUALITY_PROFILES:
            quality = DEFAULT_QUALITY
        prof = QUALITY_PROFILES[quality]
        jobs[job_id]["message"] = (
            f"AI Director: {plan.get('mood', 'cinematic')} · {zoom_intensity} zoom · "
            f"{plan.get('pacing', 'moderate')} pacing. Building sequences..."
        )

        scenes = plan.get("scenes", [])

        # Resolve per-scene durations from AI plan
        scene_durations = []
        for i in range(n):
            if i < len(scenes):
                dur = float(scenes[i].get("duration", per_image_fallback))
            else:
                dur = per_image_fallback
            scene_durations.append(max(3.0, dur))

        # If audio, precisely normalize total duration to match audio
        if audio_duration and audio_duration > 0:
            total = sum(scene_durations)
            if abs(total - audio_duration) > 0.1:
                scale = audio_duration / total
                scene_durations = [max(3.0, d * scale) for d in scene_durations]
            # Final precision fix
            total_after = sum(scene_durations)
            diff = audio_duration - total_after
            scene_durations[-1] = max(3.0, scene_durations[-1] + diff)

        # Hold time = scene duration minus transition overlap
        hold_times = [max(2.0, d - transition_duration) for d in scene_durations]
        # Clip duration = hold_time + transition overlap
        clip_durations = [ht + transition_duration for ht in hold_times]

        # ── AUDIO SYNC FIX ──────────────────────────────────────────────────────
        # Total xfade output = sum(hold_times) + transition_duration (last clip tail)
        # Ensure total video >= audio_duration + small buffer so -shortest doesn't cut early
        if audio_duration and audio_duration > 0:
            total_hold = sum(hold_times)
            expected_video_dur = total_hold + transition_duration
            if expected_video_dur < audio_duration + 0.8:
                deficit = (audio_duration + 0.8) - expected_video_dur
                hold_times[-1] += deficit
                clip_durations[-1] = hold_times[-1] + transition_duration
        # ────────────────────────────────────────────────────────────────────────

        jobs[job_id]["progress"] = 18
        jobs[job_id]["message"] = "Rendering cinematic clips..."

        clip_paths = []
        temp_dir = OUTPUT_DIR / f"tmp_{job_id}"
        temp_dir.mkdir(exist_ok=True)

        for i, img_path in enumerate(image_paths):
            clip_out = temp_dir / f"clip_{i:03d}.mp4"

            if i < len(scenes):
                motion = scenes[i].get("motion", "slow_push_in")
                if motion not in MOTION_STYLES:
                    motion = MOTION_STYLES[i % len(MOTION_STYLES)]
            else:
                motion = MOTION_STYLES[i % len(MOTION_STYLES)]

            clip_duration = clip_durations[i]
            vf = get_zoom_filter(motion, clip_duration, zoom_intensity, quality)

            cmd = [
                "ffmpeg", "-y",
                "-loop", "1",
                "-framerate", "25",
                "-threads", "1",
                "-i", img_path,
                "-vf", vf,
                "-t", f"{clip_duration:.3f}",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-r", "25",
                "-preset", prof["preset"],
                "-crf", str(prof["crf"]),
                "-x264-params", f"rc-lookahead={prof['lookahead']}:ref=1:threads=1",
                str(clip_out),
            ]

            # Popen avoids accumulating stderr in RAM for long encodes
            clip_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                _, clip_stderr = clip_proc.communicate(timeout=240)
            except subprocess.TimeoutExpired:
                clip_proc.kill()
                clip_proc.communicate()
                raise RuntimeError(f"Clip {i+1} render timed out")
            if clip_proc.returncode != 0:
                err_tail = clip_stderr[-800:].decode("utf-8", errors="replace")
                raise RuntimeError(f"Clip {i+1} render failed:\n{err_tail}")

            clip_paths.append(str(clip_out))
            pct = 18 + int((i + 1) / n * 52)
            motion_label = motion.replace("_", " ").upper()
            jobs[job_id]["progress"] = pct
            jobs[job_id]["message"] = f"Scene {i+1}/{n} — {motion_label} ({zoom_intensity} zoom)"

        jobs[job_id]["progress"] = 72
        jobs[job_id]["message"] = "Weaving cinematic transitions..."

        scene_transitions = [s.get("transition", "fade") for s in scenes]
        raw_video = temp_dir / "raw_video.mp4"
        merge_clips_with_scene_transitions(
            clip_paths, hold_times, scene_transitions, transition_duration, str(raw_video), quality
        )

        jobs[job_id]["progress"] = 88
        jobs[job_id]["message"] = "Syncing audio to picture..."

        output_path = OUTPUT_DIR / f"{job_id}.mp4"

        if audio_path and os.path.exists(audio_path):
            # Mix audio — trim video to audio end, pad audio if video is shorter
            mix_cmd = [
                "ffmpeg", "-y",
                "-i", str(raw_video),
                "-i", audio_path,
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-af", "afade=t=out:st={:.3f}:d=1.0".format(max(0, (audio_duration or 0) - 1.0)),
                "-shortest",
                "-map", "0:v:0",
                "-map", "1:a:0",
                str(output_path),
            ]
            mix_proc = subprocess.Popen(mix_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                _, mix_stderr = mix_proc.communicate(timeout=300)
            except subprocess.TimeoutExpired:
                mix_proc.kill()
                mix_proc.communicate()
                raise RuntimeError("Audio mix timed out")
            if mix_proc.returncode != 0:
                # Fallback without audio fade
                mix_cmd_fallback = [
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
                fb_proc = subprocess.Popen(mix_cmd_fallback, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                try:
                    _, fb_stderr = fb_proc.communicate(timeout=300)
                except subprocess.TimeoutExpired:
                    fb_proc.kill()
                    fb_proc.communicate()
                    raise RuntimeError("Audio mix fallback timed out")
                if fb_proc.returncode != 0:
                    err_tail = fb_stderr[-800:].decode("utf-8", errors="replace")
                    raise RuntimeError(f"Audio mix failed:\n{err_tail}")
        else:
            shutil.copy(str(raw_video), str(output_path))

        # Cleanup temp dir
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass

        jobs[job_id]["progress"] = 100
        jobs[job_id]["status"] = "done"
        jobs[job_id]["message"] = "Director's cut complete."
        jobs[job_id]["output_path"] = str(output_path)
        jobs[job_id]["filename"] = f"{job_id}.mp4"

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["message"] = str(e)
        # Cleanup on failure
        try:
            temp_dir = OUTPUT_DIR / f"tmp_{job_id}"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
        except Exception:
            pass


# ─── Routes ────────────────────────────────────────────────────────────────────

@app.route("/video-api/health")
def health():
    return jsonify({"status": "ok", "gemini": bool(GEMINI_BASE_URL), "default_quality": DEFAULT_QUALITY})


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
        if dest.exists():
            dest = session_dir / f"_{uuid.uuid4().hex[:6]}_{filename}"
        f.save(str(dest))
        saved.append(str(dest))
        original_names.append(original_name)

    if not saved:
        return jsonify({"error": "No valid images uploaded"}), 400

    sorted_paths = sort_images_by_scene(saved)

    meta = {"original_names": original_names, "sorted_paths": sorted_paths}
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
    """Pre-render AI analysis endpoint — frontend displays plan before rendering."""
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


@app.route("/video-api/chat", methods=["POST"])
def chat_director():
    """AI Chat Director — interprets natural language and updates the cinematic plan."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    message = data.get("message", "").strip()
    current_plan = data.get("current_plan", {})
    scene_names = data.get("scene_names", [])
    audio_duration = data.get("audio_duration")

    if not message:
        return jsonify({"error": "Message is required"}), 400
    if not current_plan or not current_plan.get("scenes"):
        return jsonify({"error": "A valid current_plan with scenes is required"}), 400

    updated_plan = apply_chat_directive(message, current_plan, scene_names, audio_duration)

    pacing = updated_plan.get("pacing", "moderate")
    mood = updated_plan.get("mood", "cinematic")
    zoom_intensity = updated_plan.get("zoom_intensity", "medium")
    td = updated_plan.get("transition_duration", 1.2)
    n_scenes = len(updated_plan.get("scenes", []))

    acknowledgment = (
        f"Plan updated — {pacing} pacing · {mood} mood · {zoom_intensity} zoom intensity · "
        f"{td:.1f}s transitions across {n_scenes} scenes."
    )

    return jsonify({"plan": updated_plan, "acknowledgment": acknowledgment})


@app.route("/video-api/generate", methods=["POST"])
def generate_video():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    session_id = data.get("session_id")
    audio_path = data.get("audio_path")
    audio_duration = data.get("audio_duration")
    per_image_duration = data.get("per_image_duration", 5.0)
    ai_plan = data.get("ai_plan")

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

    quality = data.get("quality", DEFAULT_QUALITY)
    if quality not in QUALITY_PROFILES:
        quality = DEFAULT_QUALITY

    options = {
        "audio_duration": audio_duration,
        "per_image_duration": float(per_image_duration),
        "ai_plan": ai_plan,
        "quality": quality,
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
