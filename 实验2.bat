@echo off
chcp 65001 >nul
echo [实验 2 · 临床实体识别 NER]
echo 1) 跑词典基线，圈出实体
call "%~dp0venv_run.bat" src\ner_baseline.py --in out\records_deid.jsonl --out out_lab2
echo.
echo 2) 自检
call "%~dp0venv_run.bat" tools\check_lab.py --lab 2 --out out_lab2
pause
