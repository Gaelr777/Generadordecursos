#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Creé .env — pon tu ANTHROPIC_API_KEY y vuelve a correr."; exit 1; }
python app.py
