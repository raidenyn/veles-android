@echo off
cd /d C:\Users\yuriy\otp23-probe\package1-p1
set EV=evidence\package1-connected-2026-10-28
python scripts\scenario_matrix.py lifecycle --scenario tab-close --out-dir %EV%\run-tab-close-2 --discover-s 30 > %EV%\run-tab-close-2-stdout.txt 2>&1
adb -s 2be02e41 shell dumpsys bluetooth_manager > %EV%\run-tab-close-2\dumpsys-2be02e41-RAW.txt 2>&1
adb -s 864a3db0 shell dumpsys bluetooth_manager > %EV%\run-tab-close-2\dumpsys-864a3db0-RAW.txt 2>&1
if exist done.tabclose2 del done.tabclose2
echo done > done.tabclose2
