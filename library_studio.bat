@echo off
REM Double-click this to open the SLAP Library Studio.
REM It starts a tiny local server (localhost only) and opens your browser.
REM Close this window when you are done editing.
cd /d "%~dp0"
python -X utf8 library_studio_server.py
if errorlevel 1 pause
