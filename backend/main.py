"""ClipForge API. Run with:  uvicorn main:app --host 0.0.0.0 --port 8000

Optional auth: set CLIPFORGE_PASSWORD to require HTTP Basic auth (any username).
Recommended whenever the port is reachable beyond your own IP."""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

import config
import jobs

_PASSWORD = os.getenv("CLIPFORGE_PASSWORD", "")
_security = HTTPBasic(auto_error=False)


def require_auth(creds: HTTPBasicCredentials | None = Depends(_security)):
    if not _PASSWORD:
        return  # auth disabled
    ok = creds is not None and secrets.compare_digest(creds.password, _PASSWORD)
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Basic"},
        )


def _check_binaries():
    import shutil
    missing = [b for b in ("ffmpeg", "ffprobe", "yt-dlp") if not shutil.which(b)]
    if missing:
        raise SystemExit(
            f"Missing required tools on this machine: {', '.join(missing)}.\n"
            "ffmpeg/ffprobe: install via your OS (apt install ffmpeg / winget "
            "install Gyan.FFmpeg). yt-dlp: pip install -r requirements.txt "
            "inside the active virtualenv."
        )


app = FastAPI(title="ClipForge", dependencies=[Depends(require_auth)])
_check_binaries()
jobs.load_saved_jobs()

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"

YT_RE = re.compile(
    r"^https?://(www\.)?(youtube\.com/(watch\?|shorts/|live/)|youtu\.be/)", re.I
)


class JobRequest(BaseModel):
    url: str
    max_clips: int = Field(default=config.MAX_CLIPS_DEFAULT, ge=1, le=10)


@app.get("/", response_class=HTMLResponse)
def index():
    return FRONTEND.read_text(encoding="utf-8")


@app.post("/api/jobs")
def create_job(req: JobRequest):
    url = req.url.strip()
    if not YT_RE.match(url):
        raise HTTPException(400, "That doesn't look like a YouTube URL.")
    job_id = jobs.create_job(url, req.max_clips)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    return job


@app.get("/api/jobs/{job_id}/clips/{index}")
def download_clip(job_id: str, index: int):
    path = jobs.clip_path(job_id, index)
    if not path:
        raise HTTPException(404, "Clip not ready.")
    job = jobs.get_job(job_id)
    title = next(
        (c["title"] for c in job["clips"] if c["index"] == index), f"clip_{index}"
    )
    safe = re.sub(r"[^\w\- ]", "", title).strip().replace(" ", "_") or f"clip_{index}"
    return FileResponse(path, media_type="video/mp4", filename=f"{safe}.mp4")
