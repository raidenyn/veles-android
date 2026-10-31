@echo off
cd /d C:\Users\yuriy\otp23-probe\package1-p1
python scripts\scenario_matrix.py lifecycle --scenario all --out-dir evidence\package1-connected-2026-10-28\lifecycle1 --discover-s 30 --connect-order scan-active > evidence\package1-connected-2026-10-28\lifecycle1-stdout.txt 2>&1
if exist done.lifecycle1 del done.lifecycle1
echo done > done.lifecycle1
