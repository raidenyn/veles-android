@echo off
cd /d C:\Users\yuriy\otp23-probe\package1-p1
set EV=evidence\package1-connected-2026-10-28
python scripts\scenario_matrix.py lifecycle --scenario shutdown --out-dir %EV%\run-shutdown-3 --discover-s 30 > %EV%\run-shutdown-3-stdout.txt 2>&1
if exist done.shut3 del done.shut3
echo done > done.shut3
