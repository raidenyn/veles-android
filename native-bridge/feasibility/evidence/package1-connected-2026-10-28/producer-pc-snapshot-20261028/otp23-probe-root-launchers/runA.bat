@echo off
cd /d C:\Users\yuriy\repos\otp23-scenarios-3dbe3db\native-bridge\feasibility
set HB=C:\Users\yuriy\repos\otp23-scenarios-3dbe3db\native-bridge\feasibility\target\release\otp23-host.exe
python scripts\scenario_matrix.py lifecycle --scenario stdin-eof --host-binary %HB% --out-dir C:\Users\yuriy\otp23-probe\direct-A1
python scripts\scenario_matrix.py lifecycle --scenario stdin-eof --host-binary %HB% --out-dir C:\Users\yuriy\otp23-probe\direct-A2
