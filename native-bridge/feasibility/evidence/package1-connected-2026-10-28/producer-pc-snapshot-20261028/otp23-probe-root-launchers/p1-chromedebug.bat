@echo off
cd /d C:\Users\yuriy\otp23-probe\package1-p1
python p1-chrome-debug.py > chrome-debug-out.txt 2>&1
if exist done.chromedebug del done.chromedebug
echo done > done.chromedebug
