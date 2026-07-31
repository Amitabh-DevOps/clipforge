"""Step 4b — cut a clip, crop to 9:16 centered on the speaker's face,
burn in captions, export 1080x1920 mp4."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import config


class RenderError(RuntimeError):
    pass


def _probe_dimensions(video_path: str) -> tuple[int, int]:
    """Display dimensions. ffmpeg auto-rotates frames per rotation metadata
    before our filters run, so swap w/h when the source is rotated 90/270."""
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:stream_side_data=rotation",
            "-of", "json", video_path,
        ],
        capture_output=True, text=True, timeout=60,
    )
    stream = json.loads(proc.stdout)["streams"][0]
    w, h = int(stream["width"]), int(stream["height"])
    rotation = 0
    for sd in stream.get("side_data_list", []):
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    if abs(rotation) % 180 == 90:
        w, h = h, w
    return w, h


def _face_keyframes(video_path: str, start: float, end: float) -> list[tuple[float, float]]:
    """Track the speaker: sample frames every ~1.5s, detect the largest face,
    return [(clip_local_time, center_x_fraction), ...]. When no face is visible
    (screen-share moments) the last known position is held, so the crop stays
    where the speaker was/will be. Best-effort: ANY failure returns a single
    centered keyframe instead of failing the render."""
    try:
        return _detect_face_keyframes(video_path, start, end)
    except Exception:
        return [(0.0, 0.5)], []


def _grab_frame(video_path: str, t: float):
    """Decode one frame at time t using the system ffmpeg (handles every codec
    YouTube serves, incl. AV1 which OpenCV's bundled decoder cannot), returned
    as an OpenCV BGR image, or None."""
    import cv2
    import numpy as np

    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", video_path,
            "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "-",
        ],
        capture_output=True, timeout=60,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    buf = np.frombuffer(proc.stdout, dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _detect_face_keyframes(video_path: str, start: float, end: float) -> list[tuple[float, float]]:
    import cv2
    import numpy as np

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    duration = end - start
    n_samples = max(8, min(24, int(duration / 1.5)))
    raw: list[float | None] = []
    times: list[float] = []
    _boxes: list[tuple] = []
    for i in range(n_samples):
        t = start + duration * (i + 0.5) / n_samples
        times.append(t - start)
        frame = _grab_frame(video_path, t)
        if frame is None:
            raw.append(None)
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, 1.15, 5, minSize=(48, 48))
        if len(faces):
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            fh, fw = frame.shape[:2]
            raw.append((x + w / 2) / fw)
            _boxes.append((x / fw, y / fh, w / fw, h / fh))
        else:
            raw.append(None)

    if not any(c is not None for c in raw):
        return [(0.0, 0.5)], []

    # Fill gaps: hold last known position; backfill the leading gap
    first = next(c for c in raw if c is not None)
    filled, last = [], first
    for c in raw:
        last = c if c is not None else last
        filled.append(last)

    # Light smoothing (moving average of 3) to kill detection jitter
    sm = [
        float(np.mean(filled[max(0, i - 1): i + 2]))
        for i in range(len(filled))
    ]
    return list(zip(times, sm)), _boxes


def _x_expression(keyframes: list[tuple[float, float]], src_w: int, crop_w: int) -> str:
    """Build an ffmpeg crop-x expression: piecewise-linear pan between
    keyframes, clamped to the frame, rounded to even pixels."""
    def px(frac: float) -> int:
        x = int(frac * src_w - crop_w / 2)
        x = max(0, min(x, src_w - crop_w))
        return x // 2 * 2

    pts = [(t, px(c)) for t, c in keyframes]
    xs = sorted(p for _, p in pts)
    # Collapse to static crop if the pan would be imperceptible (<2% width)
    if xs[-1] - xs[0] < 0.02 * src_w:
        return str(xs[len(xs) // 2])

    E = "\\,"  # comma escaped for the ffmpeg filtergraph parser
    expr = str(pts[-1][1])  # after last keyframe: hold final position
    for (t0, x0), (t1, x1) in reversed(list(zip(pts, pts[1:]))):
        seg = f"{x0}+({x1}-{x0})*(t-{t0:.2f})/{(t1 - t0):.2f}"
        expr = f"if(lt(t{E}{t1:.2f}){E}{seg}{E}{expr})"
    return f"if(lt(t{E}{pts[0][0]:.2f}){E}{pts[0][1]}{E}{expr})"


def plan_layout(source: str, start: float, end: float) -> dict:
    """Decide per-clip layout. Returns a plan dict consumed by render_clip:
    {"mode": "face"|"split", "margin_v": int, ...mode-specific fields}."""
    src_w, src_h = _probe_dimensions(source)
    target_ratio = config.OUT_WIDTH / config.OUT_HEIGHT

    if src_w / src_h <= target_ratio:
        # Vertical/square source: simple center crop, normal captions
        return {"mode": "face", "margin_v": config.CAPTION_MARGIN_V,
                "src_w": src_w, "src_h": src_h, "keyframes": [(0.0, 0.5)]}

    keyframes, boxes = _face_keyframes(source, start, end)
    median_face_h = sorted(b[3] for b in boxes)[len(boxes) // 2] if boxes else 1.0
    is_facecam = boxes and median_face_h < config.FACECAM_MAX_FACE_FRAC

    mode = config.CLIP_LAYOUT
    if mode == "auto":
        mode = "split" if is_facecam else "face"
    if mode == "split" and not boxes:
        mode = "face"  # nothing to pin in the top panel

    if mode == "face":
        return {"mode": "face", "margin_v": config.CAPTION_MARGIN_V,
                "src_w": src_w, "src_h": src_h, "keyframes": keyframes}

    # Split: median face box, expanded into a facecam region with margin
    n = len(boxes)
    med = tuple(sorted(b[i] for b in boxes)[n // 2] for i in range(4))
    fx, fy, fw, fh = med
    cx, cy = fx + fw / 2, fy + fh / 2
    # Top panel is 1080 x SPLIT_FACE_HEIGHT; crop region matches its aspect
    panel_ratio = config.OUT_WIDTH / config.SPLIT_FACE_HEIGHT
    crop_h = min(1.0, fh * 2.6)
    crop_w = min(1.0, crop_h * panel_ratio * src_h / src_w)
    crop_h = crop_w * src_w / (panel_ratio * src_h)  # re-sync after clamping
    x0 = min(max(cx - crop_w / 2, 0.0), 1.0 - crop_w)
    y0 = min(max(cy - crop_h / 2, 0.0), 1.0 - crop_h)
    face_crop = (
        int(x0 * src_w) // 2 * 2, int(y0 * src_h) // 2 * 2,
        max(2, int(crop_w * src_w) // 2 * 2), max(2, int(crop_h * src_h) // 2 * 2),
    )
    return {"mode": "split", "margin_v": config.CAPTION_MARGIN_V_SPLIT,
            "src_w": src_w, "src_h": src_h, "face_crop": face_crop}


def _audio_args() -> list[str]:
    args = []
    if config.LOUDNORM:
        args += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    return args


def render_clip(
    source: str,
    start: float,
    end: float,
    ass_path: Path,
    out_path: Path,
    plan: dict | None = None,
) -> Path:
    plan = plan or plan_layout(source, start, end)
    src_w, src_h = plan["src_w"], plan["src_h"]
    target_ratio = config.OUT_WIDTH / config.OUT_HEIGHT
    ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    subs = f"subtitles=filename='{ass_escaped}'"

    if plan["mode"] == "split":
        fx, fy, fw, fh = plan["face_crop"]
        face_h = config.SPLIT_FACE_HEIGHT
        scr_h = int(config.OUT_WIDTH * src_h / src_w) // 2 * 2
        scr_y = face_h + max(0, (config.OUT_HEIGHT - face_h - scr_h) // 2)
        fc = (
            f"[0:v]split=3[bgs][scr][fce];"
            f"[bgs]scale={config.OUT_WIDTH}:{config.OUT_HEIGHT}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={config.OUT_WIDTH}:{config.OUT_HEIGHT},"
            f"boxblur=20:2,eq=brightness=-0.2[bg];"
            f"[fce]crop={fw}:{fh}:{fx}:{fy},"
            f"scale={config.OUT_WIDTH}:{face_h}:flags=lanczos[facep];"
            f"[scr]scale={config.OUT_WIDTH}:{scr_h}:flags=lanczos[scrp];"
            f"[bg][facep]overlay=0:0[t1];"
            f"[t1][scrp]overlay=0:{scr_y}[t2];"
            f"[t2]{subs}[vout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
            "-i", source,
            "-filter_complex", fc, "-map", "[vout]", "-map", "0:a?",
            *_audio_args(),
            "-c:v", "libx264", "-preset", config.VIDEO_PRESET, "-crf", config.VIDEO_CRF,
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            str(out_path),
        ]
    else:
        if src_w / src_h > target_ratio:
            crop_w = int(src_h * target_ratio) // 2 * 2
            x_expr = _x_expression(plan["keyframes"], src_w, crop_w)
            crop = f"crop={crop_w}:{src_h}:x={x_expr}:y=0"
        else:
            crop_w = src_w
            crop_h = min(int(src_w / target_ratio) // 2 * 2, src_h)
            crop = f"crop={crop_w}:{crop_h}:0:{(src_h - crop_h) // 2}"
        vf = f"{crop},scale={config.OUT_WIDTH}:{config.OUT_HEIGHT}:flags=lanczos,{subs}"
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
            "-i", source,
            "-vf", vf,
            *_audio_args(),
            "-c:v", "libx264", "-preset", config.VIDEO_PRESET, "-crf", config.VIDEO_CRF,
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            str(out_path),
        ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise RenderError("ffmpeg timed out rendering this clip (10 min limit).")
    if proc.returncode != 0:
        raise RenderError(f"ffmpeg failed:\n{proc.stderr[-800:]}")
    return out_path
