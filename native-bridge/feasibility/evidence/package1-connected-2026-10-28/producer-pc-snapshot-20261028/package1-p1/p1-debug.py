import sys, os, traceback
sys.path.insert(0, 'scripts')
import chrome_validate as cv
try:
    print(cv.hygiene_probe([os.getpid()], os.getpid(), None))
except Exception:
    traceback.print_exc()