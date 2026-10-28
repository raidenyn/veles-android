@echo off
cd /d C:\Users\yuriy\otp23-probe\fixed
set O=C:\Users\yuriy\otp23-probe
set DB=C:\Users\yuriy\otp23-probe\diag-src2\target\release\otp23-host.exe
set L1=0838be31
set L2=4603b134
echo order start %DATE% %TIME% > %O%\final-ctl-order.txt
ping -n 40 127.0.0.1 >nul
echo R1 start %DATE% %TIME% >> %O%\final-ctl-order.txt
python scripts\chrome_relaunch_control.py --first %L1% --second %L1% --gap-s 1 --out-dir %O%\chrome-R1 > %O%\chrome-R1.stdout.txt 2>&1
echo R1 end %DATE% %TIME% >> %O%\final-ctl-order.txt
ping -n 45 127.0.0.1 >nul
echo R2 start %DATE% %TIME% >> %O%\final-ctl-order.txt
python scripts\chrome_relaunch_control.py --first %L1% --second %L2%,%L1% --gap-s 1 --out-dir %O%\chrome-R2 > %O%\chrome-R2.stdout.txt 2>&1
echo R2 end %DATE% %TIME% >> %O%\final-ctl-order.txt
ping -n 45 127.0.0.1 >nul
echo F1 start %DATE% %TIME% >> %O%\final-ctl-order.txt
python scripts\relaunch_control.py --host-binary %DB% --first %L1% --second %L1% --gap-s 1 --out-dir %O%\ctl2-F1 > %O%\ctl2-F1.stdout.txt 2>&1
ping -n 45 127.0.0.1 >nul
echo F2 start %DATE% %TIME% >> %O%\final-ctl-order.txt
python scripts\relaunch_control.py --host-binary %DB% --first %L2% --second %L2% --gap-s 1 --out-dir %O%\ctl2-F2 > %O%\ctl2-F2.stdout.txt 2>&1
ping -n 45 127.0.0.1 >nul
echo F3 start %DATE% %TIME% >> %O%\final-ctl-order.txt
python scripts\relaunch_control.py --host-binary %DB% --first %L1% --second %L1%,%L2% --gap-s 1 --out-dir %O%\ctl2-F3 > %O%\ctl2-F3.stdout.txt 2>&1
echo all end %DATE% %TIME% >> %O%\final-ctl-order.txt
echo done > %O%\final-ctl.done
