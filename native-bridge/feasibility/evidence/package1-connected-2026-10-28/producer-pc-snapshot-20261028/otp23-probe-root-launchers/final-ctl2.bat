@echo off
cd /d C:\Users\yuriy\otp23-probe\fixed
set O=C:\Users\yuriy\otp23-probe
set DB=C:\Users\yuriy\otp23-probe\diag-src2\target\release\otp23-host.exe
echo order2 start %DATE% %TIME% > %O%\final-ctl2-order.txt
echo R1 chrome any1 -^> all start %DATE% %TIME% >> %O%\final-ctl2-order.txt
python scripts\chrome_relaunch_control.py --first any1 --second all --gap-s 1 --out-dir %O%\chrome-R1b > %O%\chrome-R1b.stdout.txt 2>&1
echo R1 end %DATE% %TIME% >> %O%\final-ctl2-order.txt
ping -n 45 127.0.0.1 >nul
echo R2 chrome all -^> all start %DATE% %TIME% >> %O%\final-ctl2-order.txt
python scripts\chrome_relaunch_control.py --first all --second all --gap-s 1 --out-dir %O%\chrome-R2b > %O%\chrome-R2b.stdout.txt 2>&1
echo R2 end %DATE% %TIME% >> %O%\final-ctl2-order.txt
ping -n 45 127.0.0.1 >nul
echo F1 direct any1 -^> all start %DATE% %TIME% >> %O%\final-ctl2-order.txt
python scripts\relaunch_control.py --host-binary %DB% --first any1 --second all --gap-s 1 --out-dir %O%\ctl2-F1b > %O%\ctl2-F1b.stdout.txt 2>&1
ping -n 45 127.0.0.1 >nul
echo F2 direct all -^> all start %DATE% %TIME% >> %O%\final-ctl2-order.txt
python scripts\relaunch_control.py --host-binary %DB% --first all --second all --gap-s 1 --out-dir %O%\ctl2-F2b > %O%\ctl2-F2b.stdout.txt 2>&1
echo all end %DATE% %TIME% >> %O%\final-ctl2-order.txt
echo done > %O%\final-ctl2.done
