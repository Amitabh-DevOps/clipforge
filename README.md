# ClipForge

Self-hosted YouTube → Shorts generator. Paste a YouTube URL, get back multiple vertical (1080×1920) clips with animated word-by-word captions, chosen by Claude on AWS Bedrock. No watermark.

**Pipeline:** yt-dlp download → faster-whisper transcription (word timestamps) → Claude picks the best 15–60s moments → ffmpeg cuts, face-aware 9:16 crop, burns in captions.

---

## 1. AWS setup (one-time)

Model access is now **automatic** — the old "Model access" request page was retired (Oct 2025). All serverless models, including Claude, are enabled by default. Two things still needed:

1. **Anthropic one-time form** — Bedrock console → *Model catalog* → **Claude Sonnet 5** → open in Playground. First-time users get a short use-case form (one sentence is fine, e.g., "Generate short-form clip selections from video transcripts"). Submit once; approval is immediate.
2. **Credentials with Marketplace permissions** — first invocation of a third-party model auto-subscribes via AWS Marketplace, so the IAM role/user needs `aws-marketplace:Subscribe` too. Easiest: attach the managed policy **`AmazonBedrockFullAccess`** (covers Bedrock + Marketplace).
   - EC2: attach it via an IAM role (no keys on disk — best).
   - Local: `aws configure` with an access key for a user that has it.

Default model is `global.anthropic.claude-sonnet-5` (global cross-region profile). Override with `BEDROCK_MODEL_ID` if you want a different one.

## 2. Install & run

**EC2 (Ubuntu):**
```bash
git clone <your-repo> clipforge && cd clipforge   # or scp the folder up
bash deploy/setup_ec2.sh
cd backend && source .venv/bin/activate
export AWS_REGION=us-east-1
uvicorn main:app --host 0.0.0.0 --port 8000
```
Open port **8000** in the EC2 security group, then visit `http://<public-ip>:8000`.

For always-on: see `deploy/clipforge.service` (systemd).

**Local machine (Mac/Linux):** same steps — just install `ffmpeg` via brew/apt first.

## 3. Instance sizing

| Instance | Whisper speed (10-min video) | Cost |
|---|---|---|
| t3.large (CPU) | ~6–10 min with `small` model | cheap |
| c5.2xlarge (CPU) | ~2–4 min | medium |
| g4dn.xlarge (GPU) | ~40s with `large-v3` | ~$0.53/hr |

On GPU, set `WHISPER_MODEL=large-v3` for noticeably better captions (especially Hinglish/accented speech). **Stop the instance when not in use** — that's what burns credits.

Bedrock cost is tiny: one video ≈ a few cents of Claude Sonnet tokens.

## 4. Configuration (env vars)

| Variable | Default | Notes |
|---|---|---|
| `AWS_REGION` | `us-east-1` | Bedrock region |
| `BEDROCK_MODEL_ID` | Claude Sonnet cross-region profile | change to your enabled model |
| `WHISPER_MODEL` | `small` | `medium` / `large-v3` for better quality |
| `MAX_CLIPS_DEFAULT` | `5` | |
| `CLIP_MIN_SECONDS` / `CLIP_MAX_SECONDS` | `15` / `60` | |
| `CAPTION_FONT` | `DejaVu Sans` | any installed font |
| `CAPTION_FONT_SIZE` | `88` | |
| `CAPTION_HIGHLIGHT` | amber | ASS `&HBBGGRR` format |
| `CAPTION_MAX_WORDS` | `3` | words shown per caption page |
| `CLIPFORGE_PASSWORD` | *(empty = no auth)* | require HTTP Basic auth on all routes |

## 5. API (if you want to script it)

```
POST /api/jobs                {"url": "...", "max_clips": 5}  → {"job_id": "..."}
GET  /api/jobs/{id}           → status, stage, clips[]
GET  /api/jobs/{id}/clips/{n} → downloads the mp4
```

## 6. YouTube cookies (required on EC2)

YouTube bot-checks anonymous downloads from datacenter IPs, so on EC2 you must give yt-dlp cookies from a logged-in session:

1. On your own computer, install the open-source browser extension **"Get cookies.txt LOCALLY"** (Chrome/Edge/Firefox).
2. Open a **private/incognito window**, log in to YouTube (a secondary Google account is safest), and while on youtube.com click the extension → **Export** → save `cookies.txt`. Close the private window WITHOUT logging out (logging out invalidates the cookies).
3. Upload it: `scp -i <key.pem> cookies.txt ubuntu@<IP>:~/clipforge/backend/cookies.txt`
4. `chmod 600 ~/clipforge/backend/cookies.txt` and retry your job — the app picks it up automatically (path configurable via `YT_COOKIES_FILE`).

Cookies expire after weeks–months; if bot-check errors return, re-export. Treat the file like a password — it grants access to that Google account.

## 7. Notes & limits

- Use this for **your own videos**. Downloading others' content via yt-dlp violates YouTube's ToS and their copyright.
- Job state is saved to `backend/data/<job_id>/job.json`, so finished clips and their download links survive restarts. A job that was mid-run during a restart is marked as interrupted — just resubmit it.
- Set `CLIPFORGE_PASSWORD=<something>` to require a password (HTTP Basic, any username). Do this whenever port 8000 is open beyond your own IP — otherwise anyone can burn your credits.
- Rendered files accumulate in `backend/data/<job_id>/` — clear old folders occasionally, or add a cron job.

## 8. Ideas to extend

- Auto-generate caption/hashtag text per clip (one more Bedrock call)
- Hinglish support: Whisper handles Hindi-English mix reasonably at `large-v3`
- B-roll / zoom punch-ins on emphasis words
- Direct upload to YouTube via the Data API
