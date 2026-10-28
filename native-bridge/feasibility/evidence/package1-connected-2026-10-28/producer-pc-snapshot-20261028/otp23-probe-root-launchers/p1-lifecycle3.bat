@echo off
cd /d C:\Users\yuriy\otp23-probe\package1-p1
python scripts\scenario_matrix.py lifecycle --scenario all --out-dir evidence\package1-connected-2026-10-28\lifecycle3 --discover-s 30 --connect-order scan-active > evidence\package1-connected-2026-10-28\lifecycle3-stdout.txt 2>&1
if exist done.lifecycle3 del done.lifecycle3
echo done > done.lifecycle3
