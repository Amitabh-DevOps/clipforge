"""Job manager. Each job runs the full pipeline in a worker thread.
State is mirrored to data/<job_id>/job.json so finished jobs survive restarts
(a job mid-run when the server dies is marked as interrupted on reload)."""
from __future__ import annotations

import copy
import json
import os
import threading
import traceback
import uuid
from pathlib import Path

import config
from pipeline import analyzer, captions, cutter, downloader, transcriber

JOBS: dict[str, dict] = {}
_lock = threading.Lock()

# Heavy stages (download/transcribe/render) saturate the machine, so jobs
# beyond this limit wait in "queued" until a slot frees up.
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "1"))
_slots = threading.Semaphore(MAX_CONCURRENT_JOBS)


def _update(job_id: str, **kwargs):
    with _lock:
        JOBS[job_id].update(kwargs)
        _persist(JOBS[job_id])


def _persist(job: dict):
    try:
        d = config.DATA_DIR / job["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "job.json").write_text(json.dumps(job), encoding="utf-8")
    except OSError:
        pass  # persistence is best-effort


def load_saved_jobs():
    """Called at startup: restore finished/errored jobs from disk."""
    for f in config.DATA_DIR.glob("*/job.json"):
        try:
            job = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if job.get("stage") not in ("done", "error"):
            job["stage"] = "error"
            job["error"] = "Server restarted while this job was running. Submit it again."
        with _lock:
            JOBS.setdefault(job["id"], job)


def create_job(url: str, max_clips: int) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        JOBS[job_id] = {
            "id": job_id,
            "url": url,
            "max_clips": max_clips,
            "stage": "queued",       # queued -> downloading -> transcribing ->
                                     # analyzing -> rendering -> done | error
            "progress": 0.0,
            "video": None,
            "clips": [],
            "error": None,
        }
        _persist(JOBS[job_id])
    t = threading.Thread(target=_run, args=(job_id,), daemon=True)
    t.start()
    return job_id


def get_job(job_id: str) -> dict | None:
    with _lock:
        job = JOBS.get(job_id)
        return copy.deepcopy(job) if job else None


def _update_clip(job_id: str, index: int, **kwargs):
    """Mutate one clip's fields under the lock, then persist."""
    with _lock:
        for c in JOBS[job_id]["clips"]:
            if c["index"] == index:
                c.update(kwargs)
        _persist(JOBS[job_id])


def _run(job_id: str):
    with _slots:  # wait here (stage stays "queued") if the machine is busy
        _run_pipeline(job_id)


def _run_pipeline(job_id: str):
    job = get_job(job_id)
    job_dir = config.DATA_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Download
        _update(job_id, stage="downloading", progress=0.0)
        video = downloader.download(job["url"], job_dir)
        _update(job_id, video=video, progress=1.0)

        # 2. Transcribe
        _update(job_id, stage="transcribing", progress=0.0)
        transcript = transcriber.transcribe(
            video["path"], progress_cb=lambda p: _update(job_id, progress=p)
        )
        duration = video["duration"] or (
            transcript["segments"][-1]["end"] if transcript["segments"] else 0
        )
        if not transcript["segments"]:
            raise RuntimeError(
                "No speech detected in this video — cannot pick clips."
            )

        # 3. Analyze with Bedrock Claude
        _update(job_id, stage="analyzing", progress=0.0)
        found = analyzer.find_clips(
            video["title"], duration, transcript["segments"], job["max_clips"]
        )
        clips = [
            {**c, "index": i, "status": "pending", "file": None}
            for i, c in enumerate(found)
        ]
        _update(job_id, clips=clips, progress=1.0)

        # 4. Render each clip — one bad clip must not kill the others
        _update(job_id, stage="rendering", progress=0.0)
        ok_count = 0
        for i, clip in enumerate(clips):
            _update_clip(job_id, i, status="rendering")
            _update(job_id, progress=i / len(clips))
            out_path = job_dir / f"clip_{i}.mp4"
            try:
                ass_path = captions.build_ass(
                    transcript["words"], clip["start"], clip["end"],
                    job_dir / f"clip_{i}.ass",
                )
                cutter.render_clip(
                    video["path"], clip["start"], clip["end"], ass_path, out_path
                )
                _update_clip(job_id, i, status="done", file=str(out_path))
                ok_count += 1
            except Exception as clip_err:
                out_path.unlink(missing_ok=True)  # remove partial file
                _update_clip(job_id, i, status="failed", error=str(clip_err)[:300])
            _update(job_id, progress=(i + 1) / len(clips))

        if ok_count == 0:
            raise RuntimeError("All clips failed to render. Check ffmpeg on this machine.")
        _update(job_id, stage="done", progress=1.0)

    except Exception as e:
        traceback.print_exc()
        _update(job_id, stage="error", error=str(e))


def clip_path(job_id: str, index: int) -> Path | None:
    job = get_job(job_id)
    if not job:
        return None
    for c in job["clips"]:
        if c["index"] == index and c["file"]:
            p = Path(c["file"])
            return p if p.exists() else None
    return None
