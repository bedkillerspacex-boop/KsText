@echo off
setlocal
cd /d "%~dp0"
python kstext_publisher.py --gui
if errorlevel 1 pause
