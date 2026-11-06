#!/usr/bin/env sh
# Remove the DEVELOPMENT native bridge registration (OTP-25 Task 12) through the
# same dev-compiled maintenance engine, with the engine's ownership safeguards.
# This wrapper deletes nothing itself and the production registration is never
# an input.
#
#   remove-dev.sh --extension-id <32 a-p chars> --host-binary <path to dev host>
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python=${PYTHON:-python3}
exec "$python" -I "$here/development.py" remove "$@"
