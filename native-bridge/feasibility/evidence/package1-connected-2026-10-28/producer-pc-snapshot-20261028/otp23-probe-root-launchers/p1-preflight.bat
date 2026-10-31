@echo off
cd /d C:\Users\yuriy\otp23-probe\package1-p1
if not exist evidence\package1-connected-2026-10-28 mkdir evidence\package1-connected-2026-10-28
python scripts\package1_console_preflight.py --out evidence\package1-connected-2026-10-28\console-preflight.json > evidence\package1-connected-2026-10-28\console-preflight.stdout.txt 2>&1
if exist C:\Users\yuriy\otp23-probe\package1-p1\done.preflight del C:\Users\yuriy\otp23-probe\package1-p1\done.preflight
echo done > C:\Users\yuriy\otp23-probe\package1-p1\done.preflight
