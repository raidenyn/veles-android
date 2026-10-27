@echo off
cd /d C:\Users\yuriy\otp23-probe\tp
python timer-probe\run_probe.py --out-dir C:\Users\yuriy\otp23-probe\tp-out-smoke --hidden-s 20 > C:\Users\yuriy\otp23-probe\tp-smoke.stdout.txt 2>&1
echo exit=%ERRORLEVEL% >> C:\Users\yuriy\otp23-probe\tp-smoke.stdout.txt
echo done > C:\Users\yuriy\otp23-probe\tp-smoke.done
