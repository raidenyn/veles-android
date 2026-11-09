"""A deliberately small NSIS interpreter used ONLY to run the installer script's result-handling functions in tests.

Why this exists: there is no ``makensis`` on a Linux development host, so the Windows frontend's decision logic
(what a report means, when "Done." may be shown) could be checked only by searching its text. This module
executes the REAL ``Function`` bodies of ``windows/installer.nsi`` over a closed subset of NSIS so behavioural
fixtures (damaged inspect, successful uninstall with leftovers, ...) can assert the outcome.

What it is NOT: NSIS. It supports exactly the instructions the result-handling functions use and raises on
anything else, so a script change that leaves the subset fails loudly instead of being silently skipped. It does
not model the page flow, plug-ins, the registry or the file system, and its string semantics are a reading of
the NSIS documentation, never checked against ``makensis`` output. Native NSIS execution remains NOT RUN.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SUPPORTED = (
    "StrCpy", "StrLen", "IntOp", "IntCmp", "StrCmp", "StrCmpS", "Call", "Return", "Goto", "Pop", "Push", "DetailPrint",
    "MessageBox", "SetErrorLevel", "DeleteRegKey",
)
_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|`((?:[^`\\]|\\.)*)`|(\S+)')


class NsisError(Exception):
    """The script left the supported subset (or is malformed)."""


def _tokens(line: str) -> list[str]:
    out = []
    for match in _TOKEN.finditer(line):
        out.append(next(group for group in match.groups() if group is not None))
    return out


@dataclass
class Machine:
    functions: dict[str, list[str]]
    variables: dict[str, str] = field(default_factory=dict)
    stack: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    boxes: list[str] = field(default_factory=list)
    error_level: int | None = None
    registry_deleted: list[str] = field(default_factory=list)

    # -- strings ------------------------------------------------------------------------------
    def expand(self, text: str) -> str:
        for name in sorted(self.variables, key=len, reverse=True):
            text = text.replace(f"${name}", self.variables[name])
        return text

    def value(self, token: str) -> str:
        return self.expand(token)

    def set(self, token: str, value: str) -> None:
        if not token.startswith("$"):
            raise NsisError(f"not a variable: {token}")
        self.variables[token[1:]] = value

    # -- execution ----------------------------------------------------------------------------
    def call(self, name: str) -> None:
        if name not in self.functions:
            raise NsisError(f"unknown function {name}")
        self.run(self.functions[name])

    def run(self, body: list[str]) -> None:
        labels = {line[:-1]: index for index, line in enumerate(body) if re.fullmatch(r"[A-Za-z_][\w.]*:", line)}
        blocks = _if_blocks(body)
        pc = 0
        steps = 0
        while pc < len(body):
            steps += 1
            if steps > 200_000:
                raise NsisError("runaway script")
            line = body[pc]
            pc += 1
            if re.fullmatch(r"[A-Za-z_][\w.]*:", line):
                continue
            words = _tokens(line)
            head = words[0]
            if head == "${If}":
                if not self._condition(words[1:]):
                    pc = blocks[pc - 1]["else"] + 1 if blocks[pc - 1]["else"] is not None else blocks[pc - 1]["end"] + 1
                continue
            if head == "${Else}":
                pc = blocks[_owner(blocks, pc - 1)]["end"] + 1
                continue
            if head == "${EndIf}":
                continue
            if head not in SUPPORTED:
                raise NsisError(f"unsupported instruction: {line}")
            if head == "StrCpy":
                text = self.value(words[2])
                if len(words) > 3:
                    length = int(self.value(words[3]))
                    start = int(self.value(words[4])) if len(words) > 4 else 0
                    text = text[start:start + length] if length >= 0 else text[start:length]
                self.set(words[1], text)
            elif head == "StrLen":
                self.set(words[1], str(len(self.value(words[2]))))
            elif head == "IntOp":
                left, right = int(self.value(words[2])), int(self.value(words[4]))
                if words[3] not in ("+", "-"):
                    raise NsisError(f"unsupported IntOp {words[3]}")
                self.set(words[1], str(left + right if words[3] == "+" else left - right))
            elif head == "IntCmp":
                left, right = int(self.value(words[1])), int(self.value(words[2]))
                target = words[3] if left == right else words[4] if left < right else words[5]
                pc = self._jump(labels, target, pc)
            elif head in ("StrCmp", "StrCmpS"):
                # NSIS StrCmp ignores case, StrCmpS does not; the script uses the exact form for its closed vocabulary.
                left, right = self.value(words[1]), self.value(words[2])
                equal = left == right if head == "StrCmpS" else left.lower() == right.lower()
                target = words[3] if equal else (words[4] if len(words) > 4 else "0")
                pc = self._jump(labels, target, pc)
            elif head == "Goto":
                pc = self._jump(labels, words[1], pc)
            elif head == "Call":
                self.call(words[1])
            elif head == "Return":
                return
            elif head == "Pop":
                self.set(words[1], self.stack.pop())
            elif head == "Push":
                self.stack.append(self.value(words[1]))
            elif head == "DetailPrint":
                self.details.append(self.value(words[1]))
            elif head == "MessageBox":
                self.boxes.append(self.value(words[2]))
            elif head == "SetErrorLevel":
                self.error_level = int(self.value(words[1]))
            elif head == "DeleteRegKey":
                self.registry_deleted.append(self.value(words[-1]))

    def _jump(self, labels: dict[str, int], target: str, pc: int) -> int:
        if target in ("0", ""):
            return pc
        if target not in labels:
            raise NsisError(f"unknown label {target}")
        return labels[target]

    def _condition(self, words: list[str]) -> bool:
        if len(words) != 3 or words[1] not in ("==", "!="):
            raise NsisError(f"unsupported condition: {' '.join(words)}")
        equal = self.value(words[0]) == self.value(words[2])
        return equal if words[1] == "==" else not equal


def _owner(blocks: dict[int, dict], index: int) -> int:
    for start, block in blocks.items():
        if block["else"] == index:
            return start
    raise NsisError("stray ${Else}")


def _if_blocks(body: list[str]) -> dict[int, dict]:
    blocks: dict[int, dict] = {}
    stack: list[int] = []
    for index, line in enumerate(body):
        word = line.split()[0] if line.split() else ""
        if word == "${If}":
            blocks[index] = {"else": None, "end": None}
            stack.append(index)
        elif word == "${Else}":
            blocks[stack[-1]]["else"] = index
        elif word == "${EndIf}":
            blocks[stack.pop()]["end"] = index
    if stack:
        raise NsisError("unterminated ${If}")
    return blocks


# -- loading ----------------------------------------------------------------------------------


def _strip(line: str) -> str:
    in_quote = ""
    for index, char in enumerate(line):
        if in_quote:
            if char == in_quote:
                in_quote = ""
        elif char in "\"'`":
            in_quote = char
        elif char == ";":
            return line[:index].strip()
    return line.strip()


def load(source: str) -> Machine:
    """Parse ``Function`` bodies, expanding ``!macro`` blocks, from the script text."""
    lines = [_strip(raw) for raw in source.splitlines()]
    macros: dict[str, tuple[list[str], list[str]]] = {}
    plain: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("!macro "):
            words = line.split()
            body = []
            index += 1
            while lines[index] != "!macroend":
                body.append(lines[index])
                index += 1
            macros[words[1]] = (words[2:], body)
        else:
            plain.append(line)
        index += 1
    expanded: list[str] = []
    for line in plain:
        if line.startswith("!insertmacro "):
            words = _tokens(line)
            name = words[1]
            if name not in macros:
                continue  # library macros (GetParameters, MUI pages, ...) are outside the subset
            params, body = macros[name]
            for text in body:
                for param, argument in zip(params, words[2:]):
                    text = text.replace(f"${{{param}}}", argument)
                expanded.append(text)
        else:
            expanded.append(line)
    functions: dict[str, list[str]] = {}
    current: str | None = None
    for line in expanded:
        if line.startswith("Function "):
            current = line.split()[1]
            functions[current] = []
        elif line == "FunctionEnd":
            current = None
        elif current is not None and line:
            functions[current].append(line)
    variables = {name: "" for name in re.findall(r"^Var\s+/GLOBAL\s+(\w+)", "\n".join(lines), re.M)}
    return Machine(functions=functions, variables=variables)
