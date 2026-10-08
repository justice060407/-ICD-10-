@echo off
chcp 65001 >nul
echo [实验 4 · ICD-10 编码]
echo 1) 默认阈值跑编码
call "%~dp0venv_run.bat" src\icd_map.py --in out\records_deid.jsonl --out out_lab4
echo.
echo 2) 对照实验：调阈值看准确率怎么变
call "%~dp0venv_run.bat" src\icd_map.py --in out\records_deid.jsonl --out out_lab4 --cutoff 0.3
call "%~dp0venv_run.bat" src\icd_map.py --in out\records_deid.jsonl --out out_lab4 --cutoff 0.95
echo.
echo 3) 自检
call "%~dp0venv_run.bat" tools\check_lab.py --lab 4 --out out_lab4
pause
