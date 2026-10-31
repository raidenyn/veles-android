@echo off
rem usage: sweep.bat REP   (pairs X then Y with nominal gap g between host exit and next start)
cd /d C:\Users\yuriy\otp23-probe\fixed
set HB=C:\Users\yuriy\repos\otp23-scenarios-3dbe3db\native-bridge\feasibility\target\release\otp23-host.exe
set OUT=C:\Users\yuriy\otp23-probe\sweep-%1
for %%G in (0 2 5 10) do call :pair %%G
goto :eof
:pair
ping -n 60 127.0.0.1 >nul
python scripts\scenario_matrix.py lifecycle --scenario stdin-eof --host-binary %HB% --out-dir %OUT%\g%1-X
if not "%1"=="0" ping -n %1 127.0.0.1 >nul
python scripts\scenario_matrix.py lifecycle --scenario stdin-eof --host-binary %HB% --out-dir %OUT%\g%1-Y
goto :eof
