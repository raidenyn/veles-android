@echo off
cd /d C:\Users\yuriy\otp23-probe\package1-p1
python scripts\scenario_matrix.py lifecycle --scenario all --out-dir evidence\package1-connected-2026-10-28\lifecycle5 --discover-s 30 --connect-order scan-active > evidence\package1-connected-2026-10-28\lifecycle5-stdout.txt 2>&1
if exist done.lifecycle5 del done.lifecycle5
echo done > done.lifecycle5
