@echo off
chcp 65001 >nul
echo [实验 5 · LoRA 领域适配]
echo 1) 构建三类指令数据集
call "%~dp0venv_run.bat" src\build_sft.py --in out\records_deid.jsonl --entities out\ner_entities.jsonl --out out_lab5
echo.
echo 2) 自检
call "%~dp0venv_run.bat" tools\check_lab.py --lab 5 --out out_lab5
echo.
echo 有显卡（机房 3090 / 你的 5060）才跑训练，否则走降级分支写对比报告：
echo   python src\train_lora.py --data out_lab5\sft_train.jsonl --model Qwen\Qwen2.5-1.5B-Instruct --epochs 3 --rank 16
pause
