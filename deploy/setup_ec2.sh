#!/usr/bin/env bash
# ClipForge setup for Ubuntu 22.04/24.04 EC2. Run from the clipforge/ directory:
#   bash deploy/setup_ec2.sh
set -euo pipefail

sudo apt-get update
sudo apt-get install -y ffmpeg python3-venv python3-pip fonts-dejavu-core
# Deno: JS runtime yt-dlp uses for YouTube extraction
if ! command -v deno >/dev/null; then
  curl -fsSL https://deno.land/install.sh | sh -s -- -y >/dev/null 2>&1 || true
  [ -f "$HOME/.deno/bin/deno" ] && sudo cp "$HOME/.deno/bin/deno" /usr/local/bin/
fi

cd "$(dirname "$0")/../backend"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "Done. Start the server with:"
echo "  cd backend && source .venv/bin/activate"
echo "  export AWS_REGION=us-east-1"
echo "  uvicorn main:app --host 0.0.0.0 --port 8000"
echo ""
echo "Then open http://<EC2_PUBLIC_IP>:8000 (open port 8000 in the security group)."
