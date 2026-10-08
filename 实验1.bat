@echo off
chcp 65001 >nul
cd /d "D:\AI-Workspace\projects\clinical-llm-course"
echo [实验 1 · 合成病历与脱敏]
echo 1) 领模板（必须先自己填 5 条假病历，禁止真实病历）
copy labs\starters\my_records_template.jsonl my_lab1_records.jsonl
echo.
echo 2) 脱敏
call "%~dp0venv_run.bat" src\deid_pipeline.py --in my_lab1_records.jsonl --out out_lab1 --key "学号-lab1"
echo.
echo 3) 自检
call "%~dp0venv_run.bat" tools\check_lab.py --lab 1 --out out_lab1 --source my_lab1_records.jsonl
pause
