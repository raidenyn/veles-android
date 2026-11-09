#!/usr/bin/env sh
# Register a separately compiled DEVELOPMENT native bridge host for an unpacked
# extension (OTP-25 Task 12). Developer registration only: not distribution
# signature acceptance, and never the production host.
#
#   register-dev.sh --extension-id <32 a-p chars> --host-binary <path to dev host>
#
# The development channel (me.nagaev.veles.bridge.dev, NativeBridgeDev) is selected
# inside the dev-compiled maintenance engine; no flag can name a root, host name,
# origin, channel or manifest. All validation lives in development.py.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python=${PYTHON:-python3}
exec "$python" -I "$here/development.py" register "$@"
