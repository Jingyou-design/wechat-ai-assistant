@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" tools\check_mcp.py --wechat
pause
