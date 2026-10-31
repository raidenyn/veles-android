@echo off
cd /d C:\Users\yuriy\otp23-probe\package1-p1
set EV=evidence\package1-connected-2026-10-28
python scripts\scenario_matrix.py lifecycle --scenario shutdown --out-dir %EV%\run-shutdown --discover-s 30 > %EV%\run-shutdown-stdout.txt 2>&1
adb -s 2be02e41 shell dumpsys bluetooth_manager > %EV%\run-shutdown\dumpsys-2be02e41-RAW.txt 2>&1
adb -s 864a3db0 shell dumpsys bluetooth_manager > %EV%\run-shutdown\dumpsys-864a3db0-RAW.txt 2>&1
python scripts\scenario_matrix.py lifecycle --scenario port-disconnect --out-dir %EV%\run-port-disconnect --discover-s 30 > %EV%\run-port-disconnect-stdout.txt 2>&1
adb -s 2be02e41 shell dumpsys bluetooth_manager > %EV%\run-port-disconnect\dumpsys-2be02e41-RAW.txt 2>&1
adb -s 864a3db0 shell dumpsys bluetooth_manager > %EV%\run-port-disconnect\dumpsys-864a3db0-RAW.txt 2>&1
python scripts\scenario_matrix.py lifecycle --scenario tab-close --out-dir %EV%\run-tab-close --discover-s 30 > %EV%\run-tab-close-stdout.txt 2>&1
adb -s 2be02e41 shell dumpsys bluetooth_manager > %EV%\run-tab-close\dumpsys-2be02e41-RAW.txt 2>&1
adb -s 864a3db0 shell dumpsys bluetooth_manager > %EV%\run-tab-close\dumpsys-864a3db0-RAW.txt 2>&1
python scripts\scenario_matrix.py lifecycle --scenario failure-isolation --out-dir %EV%\run-failure-isolation --discover-s 30 > %EV%\run-failure-isolation-stdout.txt 2>&1
adb -s 2be02e41 shell dumpsys bluetooth_manager > %EV%\run-failure-isolation\dumpsys-2be02e41-RAW.txt 2>&1
adb -s 864a3db0 shell dumpsys bluetooth_manager > %EV%\run-failure-isolation\dumpsys-864a3db0-RAW.txt 2>&1
python scripts\scenario_matrix.py lifecycle --scenario stdin-eof --out-dir %EV%\run-stdin-eof --discover-s 30 > %EV%\run-stdin-eof-stdout.txt 2>&1
adb -s 2be02e41 shell dumpsys bluetooth_manager > %EV%\run-stdin-eof\dumpsys-2be02e41-RAW.txt 2>&1
adb -s 864a3db0 shell dumpsys bluetooth_manager > %EV%\run-stdin-eof\dumpsys-864a3db0-RAW.txt 2>&1
if exist done.per-scenario del done.per-scenario
echo done > done.per-scenario
