@echo off
cd /d C:\Users\yuriy\otp23-probe\tp
del C:\Users\yuriy\otp23-probe\tp\timer-probe\host-send.log 2>nul
python timer-probe\run_probe.py --out-dir C:\Users\yuriy\otp23-probe\tp-out-540 --hidden-s 540 > C:\Users\yuriy\otp23-probe\tp-540.stdout.txt 2>&1
echo exit=%ERRORLEVEL% >> C:\Users\yuriy\otp23-probe\tp-540.stdout.txt
echo done > C:\Users\yuriy\otp23-probe\tp-540.done
