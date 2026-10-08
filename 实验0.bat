@echo off
chcp 65001 >nul
echo [实验 0 · 环境验证]
echo 1) 跑通整条流水线
call "%~dp0venv_run.bat" src\run_pipeline.py --key "course-2026"
echo.
echo 2) 自检
call "%~dp0venv_run.bat" tools\check_lab.py --lab 0 --out out
pause
