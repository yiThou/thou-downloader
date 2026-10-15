@echo off
title Thou Downloading
cd /d "%~dp0"
echo Checking requirements...
pip install --upgrade flask yt-dlp telethon
cls
echo Starting Thou Downloading on http://127.0.0.1:5000 ...
python app.py
pause
