@echo off
cd /d C:\Users\yuriy\otp23-probe\fixed\scripts
python -c "import chrome_validate as cv; r=cv.ChromeRun({}); print(r.version); r.close()" > C:\Users\yuriy\otp23-probe\cr.stdout.txt 2>&1
echo exit=%ERRORLEVEL% >> C:\Users\yuriy\otp23-probe\cr.stdout.txt
echo done > C:\Users\yuriy\otp23-probe\cr.done
