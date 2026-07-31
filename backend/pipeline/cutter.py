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
        return [(0.0, 0.5)]


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
    for i in range(n_samples):
        t = start + duration * (i + 0.5) / n_samples
        times.append(t - start)
        frame = _grab_frame(video_path, t)
        if frame is None:
            raw.append(None)
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, 1.15, 5, minSize=(60, 60))
        if len(faces):
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            raw.append((x + w / 2) / frame.shape[1])
        else:
            raw.append(None)

    if not any(c is not None for c in raw):
        return [(0.0, 0.5)]

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
    return list(zip(times, sm))


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


def render_clip(
    source: str,
    start: float,
    end: float,
    ass_path: Path,
    out_path: Path,
) -> Path:
    src_w, src_h = _probe_dimensions(source)
    target_ratio = config.OUT_WIDTH / config.OUT_HEIGHT  # 9/16

    if src_w / src_h > target_ratio:
        # Landscape source: crop horizontally, keep full height,
        # panning smoothly to follow the speaker's face
        crop_w = int(src_h * target_ratio) // 2 * 2
        crop_h = src_h
        keyframes = _face_keyframes(source, start, end)
        x_expr = _x_expression(keyframes, src_w, crop_w)
        crop = f"crop={crop_w}:{crop_h}:x={x_expr}:y=0"
    else:
        # Already vertical / square: crop vertically if needed
        crop_w = src_w
        crop_h = int(src_w / target_ratio) // 2 * 2
        crop_h = min(crop_h, src_h)
        y = (src_h - crop_h) // 2
        crop = f"crop={crop_w}:{crop_h}:0:{y}"

    ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    vf = (
        f"{crop},scale={config.OUT_WIDTH}:{config.OUT_HEIGHT}:flags=lanczos,"
        f"subtitles=filename='{ass_escaped}'"
    )

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
        "-i", source,
        "-vf", vf,
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
