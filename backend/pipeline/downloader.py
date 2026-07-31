"""Step 1 — download the source video with yt-dlp."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import config


class DownloadError(RuntimeError):
    pass


def download(url: str, dest_dir: Path, progress_cb=None) -> dict:
    """Download best <=1080p mp4. Returns {"path": ..., "title": ..., "duration": ...}."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(dest_dir / "source.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
    ]
    cookies = Path(config.COOKIES_FILE)
    if cookies.exists():
        cmd += ["--cookies", str(cookies)]
    cmd += [
        # Prefer H.264 (avc1): decodable everywhere incl. OpenCV; other codecs
        # (AV1/VP9) only as fallback when no H.264 format exists
        "-f", ("bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/"
               "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/"
               "b[height<=1080][ext=mp4]/b"),
        "--merge-output-format", "mp4",
        "--print-json",
        "--no-simulate",
        "--newline",
        "-o", out_tmpl,
        url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        raise DownloadError("Download timed out after 30 minutes. Very long video or slow network.")
    if proc.returncode != 0:
        err = proc.stderr[-800:]
        if "Sign in to confirm" in proc.stderr or "not a bot" in proc.stderr:
            raise DownloadError(
                "YouTube bot-check blocked this server's IP. Fix: export a "
                "cookies.txt from a logged-in YouTube browser session and place "
                f"it at {cookies} — see the 'YouTube cookies' section of the "
                "README. Then retry the job."
            )
        raise DownloadError(
            "yt-dlp failed. The video may be private, age-restricted or the URL "
            f"is wrong.\n{err}"
        )

    # yt-dlp prints one JSON object (metadata) on stdout with --print-json
    meta = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                meta = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    files = sorted(dest_dir.glob("source.*"))
    if not files:
        raise DownloadError("Download finished but no file was produced.")
    path = files[0]

    return {
        "path": str(path),
        "title": meta.get("title", "Untitled video"),
        "duration": float(meta.get("duration") or 0.0),
    }
