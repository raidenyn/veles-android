"""OTP-24 production physical-acceptance driver building blocks (Task 10 driver). Standard library only.

Nothing here executes against a device by itself; every platform effect goes through an injected
interface so the safety properties are unit-tested with fakes. A driver run can only ever record
BLOCKED/measured facts - it never writes PASS (see recorder.py).
"""
