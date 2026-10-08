@echo off
chcp 65001 >nul
cd /d "D:\AI-Workspace\projects\clinical-llm-course"
echo [实验 3 · 病历结构化]
echo 1) 领骨架（里面有 4 个 TODO 要你自己填）
copy labs\starters\structure_skeleton.py src\structure.py
echo.
echo 2) 跑结构化（先自己写至少 30 分钟，再回来跑）
call "%~dp0venv_run.bat" src\structure.py --in out\records_deid.jsonl --out out_lab3
echo.
echo 3) 自检
call "%~dp0venv_run.bat" tools\check_lab.py --lab 3 --out out_lab3
pause
