"""Central configuration. Everything overridable via environment variables."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("CLIPFORGE_DATA", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---- AWS Bedrock ----
# Region for the Bedrock API endpoint (not necessarily where your EC2 runs).
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
# Claude Sonnet 5 via the global cross-region inference profile — best
# quality/price for transcript analysis ($2/$10 intro pricing until Aug 31 2026,
# then $3/$15). All serverless models are auto-enabled; Anthropic models just
# need a one-time use-case form on first use.
BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6"
)

# ---- YouTube download ----
# Path to a Netscape-format cookies.txt exported from a logged-in YouTube
# session. Needed on EC2/datacenter IPs where YouTube bot-checks anonymous
# downloads. If the file exists, it is used automatically.
COOKIES_FILE = os.getenv("YT_COOKIES_FILE", str(BASE_DIR / "cookies.txt"))

# ---- Whisper ----
# "small" is a good speed/quality tradeoff on CPU. Use "medium" or "large-v3"
# on a GPU instance (g4dn.xlarge) for best captions.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")  # auto / cpu / cuda
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "auto")  # int8 on cpu, float16 on gpu

# ---- Clip settings ----
MAX_CLIPS_DEFAULT = int(os.getenv("MAX_CLIPS_DEFAULT", "5"))
CLIP_MIN_SECONDS = float(os.getenv("CLIP_MIN_SECONDS", "15"))
CLIP_MAX_SECONDS = float(os.getenv("CLIP_MAX_SECONDS", "60"))

# ---- Output video ----
OUT_WIDTH = 1080
OUT_HEIGHT = 1920
VIDEO_CRF = os.getenv("VIDEO_CRF", "20")
VIDEO_PRESET = os.getenv("VIDEO_PRESET", "veryfast")

# ---- Caption styling (ASS) ----
CAPTION_FONT = os.getenv("CAPTION_FONT", "DejaVu Sans")
CAPTION_FONT_SIZE = int(os.getenv("CAPTION_FONT_SIZE", "88"))
CAPTION_HIGHLIGHT = os.getenv("CAPTION_HIGHLIGHT", "&H0024B2FF")  # amber (ASS is BGR)
CAPTION_MAX_WORDS = int(os.getenv("CAPTION_MAX_WORDS", "3"))
# Max characters per caption page (incl. spaces). Pages exceeding this get
# fewer words so text fits the screen width at the configured font size.
CAPTION_MAX_CHARS = int(os.getenv("CAPTION_MAX_CHARS", "18"))
# Semi-transparent box behind captions. ASS &HAABBGGRR; AA: 00=opaque, FF=invisible
CAPTION_BOX = os.getenv("CAPTION_BOX", "1") == "1"
CAPTION_BOX_COLOR = os.getenv("CAPTION_BOX_COLOR", "&H60000000")
# Vertical position: pixels from the bottom edge (PlayRes 1080x1920).
# 700 =~ 36% up — inside the cross-platform safe zone, clear of the
# YouTube Shorts / Instagram Reels UI overlays that cover the bottom ~20%.
CAPTION_MARGIN_V = int(os.getenv("CAPTION_MARGIN_V", "700"))
