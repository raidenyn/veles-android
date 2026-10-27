@echo off
cd /d C:\Users\yuriy\repos\otp23-scenarios-3dbe3db\native-bridge\feasibility
set HB=C:\Users\yuriy\repos\otp23-scenarios-3dbe3db\native-bridge\feasibility\target\release\otp23-host.exe
echo C immediately after failed A2 (no links were held by A2)
python scripts\scenario_matrix.py lifecycle --scenario stdin-eof --host-binary %HB% --out-dir C:\Users\yuriy\otp23-probe\direct-C
echo E after 15s gap
ping -n 15 127.0.0.1 >nul
python scripts\scenario_matrix.py lifecycle --scenario stdin-eof --host-binary %HB% --out-dir C:\Users\yuriy\otp23-probe\direct-E
echo F after 60s gap
ping -n 60 127.0.0.1 >nul
python scripts\scenario_matrix.py lifecycle --scenario stdin-eof --host-binary %HB% --out-dir C:\Users\yuriy\otp23-probe\direct-F
echo G after 180s gap
ping -n 180 127.0.0.1 >nul
python scripts\scenario_matrix.py lifecycle --scenario stdin-eof --host-binary %HB% --out-dir C:\Users\yuriy\otp23-probe\direct-G
