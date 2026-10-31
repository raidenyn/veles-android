# Gate logs 01–09 in this folder: PRE-REVIEW history (kept byte-unchanged)

The logs `01-…` through `09-…` and `ci-log-guards.sh` in this folder were produced at about 15:49–15:53Z
on 2026-10-29. That was before the final integrated review (`faee6a0e-43c5-41d7-9bc4-a2cd3f3de09b`),
which reviewed exactly this set.

**They are superseded** by the post-closure gates in `post-review/`, which ran after the F1–F3 closure.

**F3 (review finding) concerns `06-matrix.log`.** Its third header line reads:

`$ python3 scripts/matrix.py render (stdout) | diff - ../../docs/otp-23-requirements-matrix.md`

That is not the command that was executed:
- **What actually ran:** `python3 -B -c "...sys.stdout.write(m.render(m.load()))" | diff - ../../docs/otp-23-requirements-matrix.md`,
  i.e. an explicit `render(load())` comparison.
- **Why the header is wrong:** plain `matrix.py render` prints its status line instead of the
  document, so the pipeline the header describes exits 1.

The recorded `render_diff_exit_code=0` is the true result of the comparison that was executed. The
header is kept as written, as history. `post-review/06-matrix.log` logs the executed commands
literally.
