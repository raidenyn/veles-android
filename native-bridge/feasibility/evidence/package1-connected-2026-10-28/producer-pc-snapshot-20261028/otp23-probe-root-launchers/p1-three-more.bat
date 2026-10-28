@echo off
cd /d C:\Users\yuriy\otp23-probe\package1-p1
set EV=evidence\package1-connected-2026-10-28
python scripts\scenario_matrix.py lifecycle --scenario shutdown --out-dir %EV%\run-shutdown-2 --discover-s 30 > %EV%\run-shutdown-2-stdout.txt 2>&1
timeout /t 20 /nobreak >nul
adb -s 2be02e41 shell dumpsys bluetooth_manager > %EV%\run-shutdown-2\dumpsys-2be02e41-RAW.txt 2>&1
adb -s 864a3db0 shell dumpsys bluetooth_manager > %EV%\run-shutdown-2\dumpsys-864a3db0-RAW.txt 2>&1
python scripts\scenario_matrix.py lifecycle --scenario port-disconnect --out-dir %EV%\run-port-disconnect-2 --discover-s 30 > %EV%\run-port-disconnect-2-stdout.txt 2>&1
timeout /t 20 /nobreak >nul
adb -s 2be02e41 shell dumpsys bluetooth_manager > %EV%\run-port-disconnect-2\dumpsys-2be02e41-RAW.txt 2>&1
adb -s 864a3db0 shell dumpsys bluetooth_manager > %EV%\run-port-disconnect-2\dumpsys-864a3db0-RAW.txt 2>&1
python scripts\scenario_matrix.py lifecycle --scenario failure-isolation --out-dir %EV%\run-failure-isolation-2 --discover-s 30 > %EV%\run-failure-isolation-2-stdout.txt 2>&1
timeout /t 20 /nobreak >nul
adb -s 2be02e41 shell dumpsys bluetooth_manager > %EV%\run-failure-isolation-2\dumpsys-2be02e41-RAW.txt 2>&1
adb -s 864a3db0 shell dumpsys bluetooth_manager > %EV%\run-failure-isolation-2\dumpsys-864a3db0-RAW.txt 2>&1
if exist done.three del done.three
echo done > done.three
