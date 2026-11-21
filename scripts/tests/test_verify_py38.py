"""verify.py, branch_pass.py and their tests stay importable on the repository's Python floor
(3.8; check-tooling.yml asserts it). No 3.8 interpreter runs here, so these checks read the
sources instead."""
import ast
import glob
import os
import re
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS)
SOURCES = sorted([os.path.join(SCRIPTS, "verify.py"),
                  os.path.join(SCRIPTS, "branch_pass.py"),
                  os.path.join(TESTS, "test_branch_pass.py")]
                 + glob.glob(os.path.join(TESTS, "*verify*.py")))
# Builtins that became subscriptable (PEP 585) only in 3.9.
BUILTIN_GENERICS = {"tuple", "list", "dict", "set", "frozenset", "type"}
# Calls and keywords newer than 3.8: str.removeprefix/removesuffix (3.9), zip(strict=) (3.10),
# functools.cache (3.9), math.lcm (3.9).
NEWER_CALLS = re.compile(r"\.remove(?:prefix|suffix)\(|\bstrict\s*=|\bfunctools\.cache\b|\bmath\.lcm\b")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def annotation_nodes(tree):
    """Every node inside an annotation (arguments, returns, annotated assignments)."""
    found = set()
    for node in ast.walk(tree):
        roots = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            roots.extend(a.annotation for a in args.posonlyargs + args.args + args.kwonlyargs)
            roots.extend(a.annotation for a in (args.vararg, args.kwarg) if a is not None)
            roots.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
        for root in roots:
            if root is not None:
                found.update(id(n) for n in ast.walk(root))
    return found


def builtin_subscripts(tree):
    return [node for node in ast.walk(tree) if isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name) and node.value.id in BUILTIN_GENERICS]


def has_future_annotations(tree):
    return any(isinstance(node, ast.ImportFrom) and node.module == "__future__"
               and any(alias.name == "annotations" for alias in node.names) for node in tree.body)


class VerifyPython38Test(unittest.TestCase):
    def test_sources_found(self):
        self.assertIn(os.path.join(SCRIPTS, "verify.py"), SOURCES)
        self.assertGreater(len(SOURCES), 2)

    def test_syntax_parses_as_python_3_8(self):
        for path in SOURCES:
            with self.subTest(path=os.path.basename(path)):
                ast.parse(read(path), filename=path, feature_version=(3, 8))

    def test_builtin_generics_only_in_postponed_annotations(self):
        # `tuple[str, int]` raises TypeError on 3.8 wherever it is evaluated: outside annotations
        # always, inside them unless `from __future__ import annotations` postpones evaluation.
        for path in SOURCES:
            with self.subTest(path=os.path.basename(path)):
                tree = ast.parse(read(path), filename=path)
                in_annotations = annotation_nodes(tree)
                for node in builtin_subscripts(tree):
                    where = "%s:%d" % (os.path.basename(path), node.lineno)
                    self.assertIn(id(node), in_annotations, "%s: evaluated at run time" % where)
                    self.assertTrue(has_future_annotations(tree),
                                    "%s: needs from __future__ import annotations" % where)

    def test_no_calls_newer_than_3_8(self):
        for path in SOURCES:
            for number, line in enumerate(read(path).splitlines(), 1):
                code = line.split("#", 1)[0]
                with self.subTest(path=os.path.basename(path), line=number):
                    self.assertIsNone(NEWER_CALLS.search(code), line)

    def test_verify_postpones_annotations(self):
        tree = ast.parse(read(os.path.join(SCRIPTS, "verify.py")))
        self.assertTrue(has_future_annotations(tree))


if __name__ == "__main__":
    unittest.main()
