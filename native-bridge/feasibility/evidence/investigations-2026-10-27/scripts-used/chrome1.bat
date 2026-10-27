@echo off
cd /d C:\Users\yuriy\otp23-probe\fixed
set HB=C:\Users\yuriy\repos\otp23-scenarios-3dbe3db\native-bridge\feasibility\target\release\otp23-host.exe
python scripts\scenario_matrix.py lifecycle --scenario port-disconnect --host-binary %HB% --out-dir C:\Users\yuriy\otp23-probe\chrome-P1
