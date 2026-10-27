@echo off
cd /d C:\Users\yuriy\otp23-probe\fixed
set DB=C:\Users\yuriy\otp23-probe\diag-src\target\release\otp23-host.exe
python scripts\relaunch_control.py --discover --host-binary %DB% --out-dir C:\Users\yuriy\otp23-probe\ctl-discover-6t-only
