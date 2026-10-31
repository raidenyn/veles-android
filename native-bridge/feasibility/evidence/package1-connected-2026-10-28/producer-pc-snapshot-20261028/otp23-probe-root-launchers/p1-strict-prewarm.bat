@echo off
cd /d C:\Users\yuriy\otp23-probe\package1-p1
set EV=evidence\package1-connected-2026-10-28
set ADB=C:\Users\yuriy\AppData\Local\Android\Sdk\platform-tools\adb.exe
python scripts\strict_background.py --scenario prewarm --out %EV%\strict-windows-prewarm.json --phone-delay-s 690 --foreground-wait-s 600 --grace-s 30 --adb 2be02e41 --adb 864a3db0 --adb-path "%ADB%" --phone-clock-offset-ms %2 --phone-clock-uncertainty-ms %3 --phone-clock-source "%4" > %EV%\strict-prewarm-stdout.txt 2>&1
if exist done.strict-prewarm del done.strict-prewarm
echo done > done.strict-prewarm
