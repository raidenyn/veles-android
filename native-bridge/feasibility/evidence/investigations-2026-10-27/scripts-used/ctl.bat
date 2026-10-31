@echo off
cd /d C:\Users\yuriy\otp23-probe\fixed
set DB=C:\Users\yuriy\otp23-probe\diag-src\target\release\otp23-host.exe
set L1=0838be31
set L2=4603b134
set O=C:\Users\yuriy\otp23-probe
ping -n 45 127.0.0.1 >nul
echo K1 first=L1 second=L1 gap1
python scripts\relaunch_control.py --host-binary %DB% --first %L1% --second %L1% --gap-s 1 --out-dir %O%\ctl-K1
ping -n 45 127.0.0.1 >nul
echo K2 first=L1 second=L2 gap1
python scripts\relaunch_control.py --host-binary %DB% --first %L1% --second %L2% --gap-s 1 --out-dir %O%\ctl-K2
ping -n 45 127.0.0.1 >nul
echo K3 first=L1 second=L1,L2 gap1
python scripts\relaunch_control.py --host-binary %DB% --first %L1% --second %L1%,%L2% --gap-s 1 --out-dir %O%\ctl-K3
ping -n 45 127.0.0.1 >nul
echo K4 first=L1,L2 second=L1 gap1
python scripts\relaunch_control.py --host-binary %DB% --first %L1%,%L2% --second %L1% --gap-s 1 --out-dir %O%\ctl-K4
ping -n 45 127.0.0.1 >nul
echo K5 first=L1 second=L1 gap12
python scripts\relaunch_control.py --host-binary %DB% --first %L1% --second %L1% --gap-s 12 --out-dir %O%\ctl-K5
