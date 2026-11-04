@echo off
cd /d "%~dp0"
echo WeChat AI tool: http://127.0.0.1:18765/mcp
echo Keep this window open while testing. Press Ctrl+C to stop.
".venv\Scripts\python.exe" mcp_server.py --http
pause
