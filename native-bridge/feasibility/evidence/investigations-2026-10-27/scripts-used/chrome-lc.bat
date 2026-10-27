@echo off
rem Chrome-launched rapid-relaunch comparison (interactive session). C1 after idle, C2 immediately after C1, C3 after a 45 s idle.
cd /d C:\Users\yuriy\otp23-probe\fixed
set O=C:\Users\yuriy\otp23-probe\chrome-lc
mkdir %O% 2>nul
echo start %DATE% %TIME% > %O%\order.txt
python scripts\scenario_matrix.py lifecycle --scenario port-disconnect --out-dir %O%\C1 > %O%\C1.stdout.txt 2>&1
echo C1 finished %DATE% %TIME% exit=%ERRORLEVEL% >> %O%\order.txt
python scripts\scenario_matrix.py lifecycle --scenario port-disconnect --out-dir %O%\C2 > %O%\C2.stdout.txt 2>&1
echo C2 finished %DATE% %TIME% exit=%ERRORLEVEL% >> %O%\order.txt
ping -n 45 127.0.0.1 >nul
python scripts\scenario_matrix.py lifecycle --scenario port-disconnect --out-dir %O%\C3 > %O%\C3.stdout.txt 2>&1
echo C3 finished %DATE% %TIME% exit=%ERRORLEVEL% >> %O%\order.txt
echo done > C:\Users\yuriy\otp23-probe\chrome-lc.done
