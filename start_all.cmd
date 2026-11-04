@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m tools.start_all
    goto finish
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -m tools.start_all
    goto finish
)
where python >nul 2>nul
if not errorlevel 1 (
    python -m tools.start_all
    goto finish
)
echo 请先安装 Python 3.12 和 Docker Desktop，然后重新双击本文件。
:finish
popd
pause
