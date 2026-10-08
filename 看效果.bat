@echo off
chcp 65001 >nul
cd /d "D:\AI-Workspace\projects\clinical-llm-course"

echo.
echo [1/2] Running pipeline...
"D:\AI-Workspace\venvs\course\Scripts\python.exe" -X utf8 src\run_pipeline.py --key "course-2026"
if errorlevel 1 goto FAIL

echo.
echo [2/2] Building demo page...
"D:\AI-Workspace\venvs\course\Scripts\python.exe" -X utf8 tools\demo_view.py --out out --open
if errorlevel 1 goto FAIL

goto END

:FAIL
echo.
echo FAILED - see error above. Copy this window and send it to your TA.

:END
echo.
echo Press any key to close.
pause >nul
