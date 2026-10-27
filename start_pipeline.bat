@echo off
rem Starts the Naharak Salata pipeline (runs forever: makes videos, posts on schedule).
cd /d E:\Claude_Projects\ai-tiktok-pipeline
set PYTHONIOENCODING=utf-8
:loop
.venv\Scripts\python.exe -m src.main >> data\logs\service.log 2>&1
rem crashed or stopped: wait a minute and start again
timeout /t 60 /nobreak > nul
goto loop
