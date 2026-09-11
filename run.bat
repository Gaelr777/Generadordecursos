@echo off
cd /d "%~dp0"
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -q -r requirements.txt
if not exist .env ( copy .env.example .env & echo Cree .env - pon tu ANTHROPIC_API_KEY y vuelve a correr. & pause & exit /b )
python app.py
