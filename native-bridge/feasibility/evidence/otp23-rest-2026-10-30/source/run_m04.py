"""M-04 wrapper: reuse approved signed host; scoped temporary registration; no permission reset."""
import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))
import macos_install as mi
import macos_permission_recovery as driver

APP = pathlib.Path('/Users/raiden/homepc/work/otp23-cd971f0/native-bridge/feasibility/target/macos-poc/VelesOtp23PoC.app')
OUT = ROOT / 'capture'
OUT.mkdir(exist_ok=True)
manifest = mi.MANIFEST_DIR_DEFAULT / (mi.read_identity()['host_name'] + '.json')
registered = False
try:
    assert not manifest.exists(), 'Do not overwrite an existing native registration'
    assert hashlib.sha256((APP / mi.PACKAGED_BINARY).read_bytes()).hexdigest() == 'e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c'
    subprocess.run(['codesign', '--verify', '--strict', str(APP)], check=True)
    mi.register(APP, mi.MANIFEST_DIR_DEFAULT)
    registered = True
    args = argparse.Namespace(app=APP, manifest_dir=mi.MANIFEST_DIR_DEFAULT,
                              chrome=driver.macos_poc.DEFAULT_CHROME, max_seconds=1500,
                              wait_seconds=600, state_dir=OUT / 'state',
                              expect_binary_sha_prefix='e7c716c5d8c9c05', human_note=None)
    code = driver.Run(args, OUT / 'result.json').run()
    print('Driver exit', code, flush=True)
finally:
    if registered:
        mi.unregister(mi.MANIFEST_DIR_DEFAULT)
    sources = [ROOT / 'run_m04.py', ROOT / 'src/identity.rs']
    sources += list((ROOT / 'scripts').glob('macos*.py'))
    sources += list((ROOT / 'extension').glob('*'))
    (OUT / 'source-pins.json').write_text(json.dumps({str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                                   for p in sources if p.is_file()}, indent=2) + '\n')
    (OUT / 'wrapper-cleanup.json').write_text(json.dumps({
        'utc': datetime.now(timezone.utc).isoformat(), 'registration_absent': not manifest.exists(),
        'binary_sha256_after': hashlib.sha256((APP / mi.PACKAGED_BINARY).read_bytes()).hexdigest()
    }, indent=2) + '\n')
