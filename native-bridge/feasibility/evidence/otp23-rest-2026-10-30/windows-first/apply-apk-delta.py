"""Transport-only BSDIFF40 decoder; verify base, delta and reconstructed APK hashes."""
import bz2
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parent
metadata = json.loads((root / 'a01-transfer.json').read_text())
base = pathlib.Path(sys.argv[1]).read_bytes()
patch = (root / 'a01-test.bsdiff').read_bytes()
assert hashlib.sha256(base).hexdigest() == metadata['base_sha256']
assert hashlib.sha256(patch).hexdigest() == metadata['patch_sha256']
assert patch[:8] == b'BSDIFF40'

def integer(value):
    number = int.from_bytes(value, 'little')
    magnitude = number & ((1 << 63) - 1)
    return -magnitude if number & (1 << 63) else magnitude

control_length, diff_length, size = [integer(patch[i:i + 8]) for i in (8, 16, 24)]
assert control_length >= 0 and diff_length >= 0 and size == metadata['target_bytes']
control = bz2.decompress(patch[32:32 + control_length])
diff = bz2.decompress(patch[32 + control_length:32 + control_length + diff_length])
extra = bz2.decompress(patch[32 + control_length + diff_length:])
output = bytearray(size)
old_position = new_position = control_position = diff_position = extra_position = 0
while new_position < size:
    assert control_position + 24 <= len(control)
    x, y, z = [integer(control[i:i + 8]) for i in range(control_position, control_position + 24, 8)]
    control_position += 24
    assert x >= 0 and y >= 0 and new_position + x + y <= size and diff_position + x <= len(diff)
    for i in range(x):
        old = base[old_position + i] if 0 <= old_position + i < len(base) else 0
        output[new_position + i] = (old + diff[diff_position + i]) & 255
    old_position += x
    new_position += x
    diff_position += x
    assert extra_position + y <= len(extra)
    output[new_position:new_position + y] = extra[extra_position:extra_position + y]
    new_position += y
    extra_position += y
    old_position += z
assert hashlib.sha256(output).hexdigest() == metadata['target_sha256']
destination = pathlib.Path(sys.argv[2])
assert not destination.exists(), 'Do not overwrite an existing artifact'
destination.write_bytes(output)
print(json.dumps({'base_sha256': metadata['base_sha256'], 'reconstructed_sha256': hashlib.sha256(output).hexdigest(),
                  'bytes': len(output), 'verified': True}))
