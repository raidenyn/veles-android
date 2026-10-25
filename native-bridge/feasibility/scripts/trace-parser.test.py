"""Pure regression test for physical_validate.parse_trace (no Windows imports).

The chrome_validate module used by physical_validate only imports on Windows, so this test
extracts just the parse_trace function from the source with ast and executes it against
synthetic trace files. It guards three things: MTUs are keyed by the host connection token
only (no per-handle/connected-line association is inferred), the send/reply timeline parses,
and empty/missing traces yield empty results instead of fabricated values.

Run: python3 scripts/trace-parser.test.py  (exit 1 on any failure)
"""

import ast
import json
import pathlib
import re
import tempfile

HERE = pathlib.Path(__file__).resolve().parent


def extract_parse_trace():
    source = (HERE / "physical_validate.py").read_text(encoding="utf-8")
    functions = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef) and node.name == "parse_trace"]
    assert len(functions) == 1, "expected exactly one parse_trace definition"
    namespace = {"re": re, "pathlib": pathlib}
    exec(compile(ast.Module(body=functions, type_ignores=[]),
                 "physical_validate.py[parse_trace]", "exec"), namespace)
    return namespace["parse_trace"]


parse_trace = extract_parse_trace()

# Interleaved operation: both conn tokens report an MTU, conn 3 reports twice (later wins),
# connected events carry connection handles, and connected/subscribe lines interleave with
# conn tokens of the *other* connection. A trace must never associate those handles with a
# conn token, so none of them may appear anywhere in the parsed output.
SAMPLE = (
    "[       0ms pid=87] start pid=87\n"
    "[     391ms pid=87] in  len=137 type=start_scan request_id=r2-viyolw api_version=1\n"
    "[    1242ms pid=87] conn 3 mtu=517 at 1242ms\n"
    "[    1500ms pid=87] conn 2 service at 1500ms\n"
    "[    1513ms pid=87] conn 2 subscribed at 1513ms\n"
    "[    1632ms pid=87] conn 3 mtu=517 at 1632ms\n"
    "[    2956ms pid=87] out len=160 type=connected request_id=r3-msc73p api_version=1"
    " connection_handle=600e794c80e380a7 platform_hint.len=20\n"
    "[    3083ms pid=87] conn 2 mtu=517 at 1632ms\n"
    "[    3543ms pid=87] in  len=143 type=send request_id=r6-p6kicr api_version=1"
    " connection_handle=37dd8a389e3bb535 record_base64.len=12\n"
    "[    3568ms pid=87] out len=121 type=message api_version=1"
    " connection_handle=600e794c80e380a7 record_base64.len=12\n"
    "[   16700ms pid=87] conn 2 released\n"
)


def expect(name, condition, detail=""):
    if not condition:
        raise AssertionError(f"{name}: {detail}")
    print(f"pass {name}")


def main():
    with tempfile.TemporaryDirectory(prefix="trace-parser-test-") as scratch:
        root = pathlib.Path(scratch)
        sample = root / "sample.log"
        sample.write_text(SAMPLE, encoding="utf-8")

        parsed = parse_trace(sample)
        expect("sample-keys", parsed.keys() == {"mtu_by_conn", "sends", "replies"}, parsed.keys())
        expect("sample-mtu", parsed["mtu_by_conn"] == {3: 517, 2: 517}, parsed["mtu_by_conn"])
        expect("sample-sends", parsed["sends"] == [{"at_ms": 3543, "record_base64_len": 12}],
               parsed["sends"])
        expect("sample-replies", parsed["replies"] == [{"at_ms": 3568, "record_base64_len": 12}],
               parsed["replies"])
        expect("sample-no-handles",
               "600e794c80e380a7" not in repr(parsed) and "37dd8a389e3bb535" not in repr(parsed),
               repr(parsed))
        expect("sample-json", "mtu_by_conn" in json.dumps(parsed))

        empty_file = root / "empty.log"
        empty_file.write_text("", encoding="utf-8")
        for name, path, expected in (
            ("empty-file", empty_file, {"mtu_by_conn": {}, "sends": [], "replies": []}),
            ("missing-file", root / "absent.log", {"mtu_by_conn": {}, "sends": [], "replies": []}),
        ):
            expect(name, parse_trace(path) == expected, parse_trace(path))


if __name__ == "__main__":
    main()