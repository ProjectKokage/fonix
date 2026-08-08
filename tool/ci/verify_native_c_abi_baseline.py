#!/usr/bin/env python3
"""Verify Fonix's canonical native C ABI baseline.

The native-header baseline is intentionally structural: comments and formatting
do not affect it, while every header token and parsed ABI declaration does. The
executable Python export checker is additionally source-integrity-bound so an
indirect mutation cannot silently diverge from its reviewed literal allowlist.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


BASELINE_PATH = "release/native-c-abi-v1.json"
HEADER_PATH = "src/dort.h"
ELF_EXPORT_PATH = "src/fonix_exports.map"
APPLE_EXPORT_PATH = "src/fonix_exports.apple"
WINDOWS_EXPORT_PATH = "src/fonix_shim.def"
ALLOWLIST_PATH = "test/native/check_exports.py"
NATIVE_CMAKE_PATH = "test/native/CMakeLists.txt"

MAX_SOURCE_BYTES = 256 * 1024
MAX_BASELINE_BYTES = 2 * 1024 * 1024
MAX_DIFF_LINES = 240
EXPECTED_FUNCTION_COUNT = 67
CLAIM_BOUNDARY = (
    "This record freezes the reviewed native C ABI structure only. It does not "
    "approve a Dart API, prove binary compatibility on a target, authorize "
    "publication, or replace external API/ABI approval."
)

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_MACRO = re.compile(
    rf"^\s*#define\s+(DORT_[A-Z0-9_]+)(?:[ \t]+([^\n]*?))?[ \t]*$",
    re.MULTILINE,
)
_OPAQUE = re.compile(
    rf"\btypedef\s+struct\s+({_IDENTIFIER})\s+({_IDENTIFIER})\s*;"
)
_ENUM = re.compile(
    rf"\btypedef\s+enum\s+({_IDENTIFIER})\s*\{{(.*?)\}}\s*({_IDENTIFIER})\s*;",
    re.DOTALL,
)
_STRUCT = re.compile(
    rf"\btypedef\s+struct\s+({_IDENTIFIER})\s*\{{(.*?)\}}\s*({_IDENTIFIER})\s*;",
    re.DOTALL,
)
_FUNCTION = re.compile(
    rf"^DORT_API[ \t]+"
    rf"(?P<return>{_IDENTIFIER}(?:[ \t]+{_IDENTIFIER}|[ \t]*\*)*)"
    rf"[ \t]+DORT_CALL\s+"
    rf"(?P<name>dort_[a-z0-9_]+)\s*"
    rf"\((?P<parameters>.*?)\)\s*;",
    re.MULTILINE | re.DOTALL,
)


class NativeAbiBaselineError(RuntimeError):
    """The native ABI sources or committed baseline violate the contract."""


def _splice_c_lines(source: str) -> str:
    """Apply C translation-phase line splicing before comment removal."""

    if re.search(r"\?\?[=/'()!<>-]", source) is not None:
        raise NativeAbiBaselineError("the native ABI header may not contain C trigraphs")
    return source.replace("\\\r\n", "").replace("\\\n", "")


def _strip_comments(source: str) -> str:
    result: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if quote is not None:
            result.append(character)
            if character == "\\" and following:
                result.append(following)
                index += 2
                continue
            if character == quote:
                quote = None
            index += 1
            continue
        if character in {'"', "'"}:
            quote = character
            result.append(character)
            index += 1
            continue
        if character == "/" and following == "/":
            result.extend((" ", " "))
            index += 2
            while index < len(source) and source[index] != "\n":
                result.append(" ")
                index += 1
            continue
        if character == "/" and following == "*":
            result.extend((" ", " "))
            index += 2
            while index < len(source):
                character = source[index]
                following = source[index + 1] if index + 1 < len(source) else ""
                if character == "*" and following == "/":
                    result.extend((" ", " "))
                    index += 2
                    break
                result.append("\n" if character == "\n" else " ")
                index += 1
            else:
                raise NativeAbiBaselineError(
                    "the native ABI header has an unterminated comment"
                )
            continue
        result.append(character)
        index += 1
    return "".join(result)


def _canonical_tokens(value: str) -> str:
    tokens = re.findall(
        r'(?:u8|u|U|L)?"(?:\\.|[^"\\])*"|'
        r"(?:u|U|L)?'(?:\\.|[^'\\])*'|"
        r"(?:\.[0-9]|[0-9])(?:[eEpP][+-]|[A-Za-z0-9_.])*|"
        r"[A-Za-z_][A-Za-z0-9_]*|"
        r"%:%:|>>=|<<=|\.\.\.|->|\+\+|--|<<|>>|<=|>=|==|!=|"
        r"&&|\|\||\*=|/=|%=|\+=|-=|&=|\^=|\|=|##|<:|:>|<%|%>|%:|"
        r"[^\s]",
        value,
    )
    if not tokens and value.strip():
        raise NativeAbiBaselineError("a C declaration could not be tokenized")
    return " ".join(tokens)


def _preprocessor_directives(source: str) -> list[str]:
    """Return every logical directive, including non-DORT ABI influences."""

    lines = source.splitlines()
    directives: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.lstrip().startswith("#"):
            index += 1
            continue
        logical_lines = [line]
        while logical_lines[-1].endswith("\\"):
            index += 1
            if index >= len(lines):
                raise NativeAbiBaselineError(
                    "a preprocessor directive ends with an incomplete continuation"
                )
            logical_lines.append(lines[index])
        directives.append(_canonical_tokens("\n".join(logical_lines)))
        index += 1
    if not directives:
        raise NativeAbiBaselineError("the native ABI header has no directives")
    return directives


def _split_top_level(value: str, delimiter: str, *, label: str) -> list[str]:
    parts: list[str] = []
    start = 0
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for index, character in enumerate(value):
        if character in "([{":
            stack.append(character)
        elif character in ")]}":
            if not stack or stack.pop() != pairs[character]:
                raise NativeAbiBaselineError(f"{label} has unbalanced delimiters")
        elif character == delimiter and not stack:
            part = value[start:index].strip()
            if not part:
                raise NativeAbiBaselineError(f"{label} has an empty entry")
            parts.append(part)
            start = index + 1
    if stack:
        raise NativeAbiBaselineError(f"{label} has unbalanced delimiters")
    final = value[start:].strip()
    if final:
        parts.append(final)
    elif value.strip() and not value.rstrip().endswith(delimiter):
        raise NativeAbiBaselineError(f"{label} has an empty final entry")
    return parts


def _read_regular(path: Path, *, label: str, maximum: int) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise NativeAbiBaselineError(f"{label} cannot be inspected") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise NativeAbiBaselineError(f"{label} must be a regular file, not a link")
    if before.st_size < 0 or before.st_size > maximum:
        raise NativeAbiBaselineError(f"{label} size is outside the accepted bound")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise NativeAbiBaselineError(f"{label} cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if not stat.S_ISREG(opened.st_mode) or identity != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ):
            raise NativeAbiBaselineError(f"{label} changed while being opened")
        chunks: list[bytes] = []
        consumed = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - consumed))
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
            if consumed > maximum:
                raise NativeAbiBaselineError(f"{label} exceeds the accepted bound")
        after = os.fstat(descriptor)
        if consumed != before.st_size or identity != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise NativeAbiBaselineError(f"{label} changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _read_text(path: Path, *, label: str, maximum: int = MAX_SOURCE_BYTES) -> str:
    raw = _read_regular(path, label=label, maximum=maximum)
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise NativeAbiBaselineError(f"{label} must be strict UTF-8") from error


def _platform_macro_directives(source: str) -> list[str]:
    start = source.find("#if defined(_WIN32)")
    end = source.find("#ifdef __cplusplus", start)
    if start < 0 or end < 0:
        raise NativeAbiBaselineError("the platform ABI macro block is missing")
    result = [
        _canonical_tokens(line)
        for line in source[start:end].splitlines()
        if line.strip()
    ]
    if not result or result[0] != "# if defined ( _WIN32 )":
        raise NativeAbiBaselineError("the platform ABI macro block is malformed")
    return result


def _c_linkage_directives(source: str) -> list[dict[str, Any]]:
    block = re.compile(
        r"^[ \t]*#ifdef[ \t]+__cplusplus[ \t]*\n"
        r"(?P<body>.*?)"
        r"^[ \t]*#endif[ \t]*$",
        re.MULTILINE | re.DOTALL,
    )
    matches = list(block.finditer(source))
    directives = [
        [
            _canonical_tokens("#ifdef __cplusplus"),
            _canonical_tokens(match.group("body")),
            _canonical_tokens("#endif"),
        ]
        for match in matches
    ]
    expected = [
        ["# ifdef __cplusplus", 'extern "C" {', "# endif"],
        ["# ifdef __cplusplus", "}", "# endif"],
    ]
    first_declaration = source.find("#define DORT_ABI_VERSION")
    final_function = source.rfind("DORT_API")
    if (
        directives != expected
        or first_declaration < 0
        or final_function < 0
        or matches[0].end() > first_declaration
        or matches[1].start() < final_function
    ):
        raise NativeAbiBaselineError("the C++ linkage guards are malformed")
    return [
        {
            "position": "beforePublicDeclarations",
            "directives": directives[0],
        },
        {
            "position": "afterPublicDeclarations",
            "directives": directives[1],
        },
    ]


def _macros(source: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    define_lines = re.findall(r"^\s*#define\s+DORT_[^\n]*$", source, re.MULTILINE)
    matches = list(_MACRO.finditer(source))
    if len(matches) != len(define_lines):
        raise NativeAbiBaselineError("a public DORT macro has unsupported syntax")

    abi_macros: list[dict[str, str]] = []
    constants: list[dict[str, str]] = []
    constant_names: set[str] = set()
    for match in matches:
        name = match.group(1)
        value = _canonical_tokens(match.group(2) or "")
        entry = {"name": name, "value": value}
        if name in {"DORT_API", "DORT_CALL"}:
            abi_macros.append(entry)
            continue
        if name in constant_names:
            raise NativeAbiBaselineError(f"ABI constant is duplicated: {name}")
        constant_names.add(name)
        constants.append(entry)
    if not abi_macros or not constants:
        raise NativeAbiBaselineError("the public ABI macro inventory is empty")
    return abi_macros, constants


def _opaque_types(source: str) -> list[dict[str, str]]:
    result = [
        {"tag": match.group(1), "alias": match.group(2)}
        for match in _OPAQUE.finditer(source)
    ]
    if len({entry["alias"] for entry in result}) != len(result):
        raise NativeAbiBaselineError("an opaque ABI type is duplicated")
    return result


def _enums(source: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    aliases: set[str] = set()
    for match in _ENUM.finditer(source):
        tag, body, alias = match.groups()
        if alias in aliases:
            raise NativeAbiBaselineError(f"enum alias is duplicated: {alias}")
        aliases.add(alias)
        values: list[dict[str, str]] = []
        names: set[str] = set()
        for raw_entry in _split_top_level(body, ",", label=f"enum {alias}"):
            entry = re.fullmatch(rf"\s*({_IDENTIFIER})\s*=\s*(.+?)\s*", raw_entry)
            if entry is None:
                raise NativeAbiBaselineError(
                    f"enum {alias} must give every value an explicit expression"
                )
            name = entry.group(1)
            if name in names:
                raise NativeAbiBaselineError(f"enum {alias} duplicates {name}")
            names.add(name)
            values.append({"name": name, "value": _canonical_tokens(entry.group(2))})
        if not values:
            raise NativeAbiBaselineError(f"enum {alias} has no values")
        result.append({"tag": tag, "alias": alias, "values": values})
    return result


def _structs(source: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    aliases: set[str] = set()
    for match in _STRUCT.finditer(source):
        tag, body, alias = match.groups()
        if alias in aliases:
            raise NativeAbiBaselineError(f"struct alias is duplicated: {alias}")
        aliases.add(alias)
        fields: list[dict[str, str]] = []
        names: set[str] = set()
        for raw_field in _split_top_level(body, ";", label=f"struct {alias}"):
            declaration = _canonical_tokens(raw_field)
            identifiers = re.findall(_IDENTIFIER, declaration)
            if len(identifiers) < 2:
                raise NativeAbiBaselineError(
                    f"struct {alias} contains an unsupported field declaration"
                )
            name = identifiers[-1]
            if name in names:
                raise NativeAbiBaselineError(f"struct {alias} duplicates field {name}")
            names.add(name)
            fields.append({"name": name, "declaration": declaration})
        if not fields:
            raise NativeAbiBaselineError(f"struct {alias} has no fields")
        result.append({"tag": tag, "alias": alias, "fields": fields})
    return result


def _functions(source: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for match in _FUNCTION.finditer(source):
        name = match.group("name")
        if name in names:
            raise NativeAbiBaselineError(f"function is duplicated: {name}")
        names.add(name)
        raw_parameters = match.group("parameters").strip()
        if _canonical_tokens(raw_parameters) == "void":
            parameters = ["void"]
        else:
            parameters = [
                _canonical_tokens(parameter)
                for parameter in _split_top_level(
                    raw_parameters, ",", label=f"function {name} parameters"
                )
            ]
        result.append(
            {
                "name": name,
                "returnType": _canonical_tokens(match.group("return")),
                "callingConvention": "DORT_CALL",
                "parameters": parameters,
            }
        )
    declarations = re.findall(r"^DORT_API\b", source, re.MULTILINE)
    if len(result) != len(declarations):
        raise NativeAbiBaselineError("a DORT_API declaration has unsupported syntax")
    function_like_names = re.findall(r"\b(dort_[a-z0-9_]+)\s*\(", source)
    if function_like_names != [entry["name"] for entry in result]:
        raise NativeAbiBaselineError(
            "the header contains an unexported or unsupported dort_ function declaration"
        )
    if len(result) != EXPECTED_FUNCTION_COUNT:
        raise NativeAbiBaselineError(
            f"native ABI must contain exactly {EXPECTED_FUNCTION_COUNT} functions"
        )
    return result


def _elf_exports(source: str) -> tuple[str, list[str]]:
    match = re.fullmatch(
        r"\s*([A-Z][A-Z0-9_.]*)\s*\{\s*global:\s*"
        r"((?:dort_[a-z0-9_]+\s*;\s*)+)"
        r"local:\s*\*\s*;\s*\}\s*;\s*",
        source,
    )
    if match is None:
        raise NativeAbiBaselineError("the ELF export map is not a closed allowlist")
    exports = re.findall(r"(dort_[a-z0-9_]+)\s*;", match.group(2))
    if len(exports) != len(set(exports)):
        raise NativeAbiBaselineError("the ELF export map contains a duplicate")
    return match.group(1), exports


def _apple_exports(source: str) -> list[str]:
    lines = source.splitlines()
    if not lines or any(re.fullmatch(r"_dort_[a-z0-9_]+", line) is None for line in lines):
        raise NativeAbiBaselineError("the Apple export list is malformed")
    exports = [line[1:] for line in lines]
    if len(exports) != len(set(exports)):
        raise NativeAbiBaselineError("the Apple export list contains a duplicate")
    return exports


def _windows_exports(source: str) -> tuple[str, list[str]]:
    lines = source.splitlines()
    if len(lines) < 3 or re.fullmatch(r"LIBRARY ([A-Za-z0-9_]+)", lines[0]) is None:
        raise NativeAbiBaselineError("the Windows definition header is malformed")
    if lines[1] != "EXPORTS":
        raise NativeAbiBaselineError("the Windows definition file lacks EXPORTS")
    if any(re.fullmatch(r"  dort_[a-z0-9_]+", line) is None for line in lines[2:]):
        raise NativeAbiBaselineError("the Windows export list is malformed")
    exports = [line[2:] for line in lines[2:]]
    if len(exports) != len(set(exports)):
        raise NativeAbiBaselineError("the Windows export list contains a duplicate")
    return lines[0].split(" ", 1)[1], exports


def _python_allowlist(source: str) -> tuple[set[str], str]:
    try:
        tree = ast.parse(source, filename=ALLOWLIST_PATH, mode="exec")
    except SyntaxError as error:
        raise NativeAbiBaselineError("the Python export allowlist is invalid") from error
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "ALLOWED" for target in node.targets)
    ]
    if len(assignments) != 1 or not isinstance(assignments[0].value, ast.Set):
        raise NativeAbiBaselineError("the Python ALLOWED export set is not one literal set")
    assignment = assignments[0]
    if (
        len(assignment.targets) != 1
        or not isinstance(assignment.targets[0], ast.Name)
        or assignment.targets[0].id != "ALLOWED"
    ):
        raise NativeAbiBaselineError(
            "the Python ALLOWED export set must have one direct assignment"
        )

    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    assignment_target = assignment.targets[0]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name) or node.id != "ALLOWED":
            continue
        if node is assignment_target:
            continue
        parent = parents.get(id(node))
        is_set_difference = isinstance(parent, ast.BinOp) and isinstance(
            parent.op, ast.Sub
        )
        is_length_read = (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id == "len"
            and parent.args == [node]
            and not parent.keywords
        )
        if not isinstance(node.ctx, ast.Load) or not (
            is_set_difference or is_length_read
        ):
            raise NativeAbiBaselineError(
                "the Python ALLOWED export set has an unsupported mutation or use"
            )

    elements = assignment.value.elts
    values: list[str] = []
    for element in elements:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            raise NativeAbiBaselineError("the Python ALLOWED set must contain strings")
        if re.fullmatch(r"dort_[a-z0-9_]+", element.value) is None:
            raise NativeAbiBaselineError("the Python ALLOWED set has an invalid symbol")
        values.append(element.value)
    if len(values) != len(set(values)):
        raise NativeAbiBaselineError("the Python ALLOWED set contains a duplicate")
    return set(values), hashlib.sha256(source.encode("utf-8")).hexdigest()


def _python_audit_invocations(source: str) -> list[dict[str, str]]:
    scripts = (
        ("exportAllowlist", "check_exports.py"),
        ("externalDependency", "check_no_ort_dependency.py"),
    )
    result: list[dict[str, str]] = []
    for identifier, script in scripts:
        expression = re.compile(
            r'"\$\{Python3_EXECUTABLE\}"\s*'
            r'-I\s*'
            rf'"\$\{{CMAKE_CURRENT_LIST_DIR\}}/{re.escape(script)}"'
        )
        if len(expression.findall(source)) != 1 or source.count(script) != 1:
            raise NativeAbiBaselineError(
                f"the {script} native audit must use one isolated Python invocation"
            )
        result.append(
            {
                "id": identifier,
                "script": f"test/native/{script}",
                "pythonMode": "isolated",
            }
        )
    return result


def _constant_integer(constants: list[dict[str, str]], name: str) -> int:
    matches = [entry["value"] for entry in constants if entry["name"] == name]
    if len(matches) != 1 or re.fullmatch(r"[0-9]+[uU]", matches[0]) is None:
        raise NativeAbiBaselineError(f"{name} must be one unsigned decimal literal")
    return int(matches[0][:-1])


def build_contract(repository: Path) -> dict[str, Any]:
    repository = repository.resolve(strict=True)
    if repository.is_symlink() or not repository.is_dir():
        raise NativeAbiBaselineError("repository must be a directory, not a link")
    header = _strip_comments(
        _splice_c_lines(
            _read_text(repository / HEADER_PATH, label="native ABI header")
        )
    )
    header_tokens = _canonical_tokens(header)
    preprocessor_directives = _preprocessor_directives(header)
    abi_macros, constants = _macros(header)
    opaque_types = _opaque_types(header)
    enums = _enums(header)
    structs = _structs(header)
    functions = _functions(header)
    typedef_count = len(re.findall(r"\btypedef\b", header))
    if typedef_count != len(opaque_types) + len(enums) + len(structs):
        raise NativeAbiBaselineError("the header contains an unsupported typedef form")

    elf_version, elf_exports = _elf_exports(
        _read_text(repository / ELF_EXPORT_PATH, label="ELF export map")
    )
    apple_exports = _apple_exports(
        _read_text(repository / APPLE_EXPORT_PATH, label="Apple export list")
    )
    windows_library, windows_exports = _windows_exports(
        _read_text(repository / WINDOWS_EXPORT_PATH, label="Windows definition file")
    )
    allowlist, allowlist_source_sha256 = _python_allowlist(
        _read_text(repository / ALLOWLIST_PATH, label="Python export allowlist")
    )
    native_cmake = _read_text(
        repository / NATIVE_CMAKE_PATH,
        label="native CTest CMake file",
    )
    python_audits = _python_audit_invocations(native_cmake)
    function_names = [function["name"] for function in functions]
    for label, exports in (
        ("ELF", elf_exports),
        ("Apple", apple_exports),
        ("Windows", windows_exports),
    ):
        if exports != function_names:
            raise NativeAbiBaselineError(
                f"the {label} export list differs from ordered header functions"
            )
    if allowlist != set(function_names):
        raise NativeAbiBaselineError(
            "the Python export allowlist differs from header functions"
        )

    return {
        "sourcePaths": {
            "header": HEADER_PATH,
            "elfExportMap": ELF_EXPORT_PATH,
            "appleExportList": APPLE_EXPORT_PATH,
            "windowsDefinition": WINDOWS_EXPORT_PATH,
            "pythonAllowlist": ALLOWLIST_PATH,
            "nativeCTestCMake": NATIVE_CMAKE_PATH,
        },
        "abiVersion": _constant_integer(constants, "DORT_ABI_VERSION"),
        "ortApiCompatibilityFloor": _constant_integer(
            constants, "DORT_ORT_API_COMPATIBILITY_FLOOR"
        ),
        "canonicalHeaderSha256": hashlib.sha256(
            header_tokens.encode("utf-8")
        ).hexdigest(),
        "preprocessorDirectives": preprocessor_directives,
        "elfSymbolVersion": elf_version,
        "windowsLibrary": windows_library,
        "pythonAllowlistSourceSha256": allowlist_source_sha256,
        "nativeCTestCMakeSha256": hashlib.sha256(
            native_cmake.encode("utf-8")
        ).hexdigest(),
        "pythonAuditInvocations": python_audits,
        "platformMacroDirectives": _platform_macro_directives(header),
        "cLinkageDirectives": _c_linkage_directives(header),
        "abiMacroDefinitions": abi_macros,
        "abiConstants": constants,
        "opaqueTypes": opaque_types,
        "enums": enums,
        "structs": structs,
        "functions": functions,
        "exports": function_names,
    }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _render_baseline(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def build_baseline(repository: Path) -> dict[str, Any]:
    contract = build_contract(repository)
    return {
        "schemaVersion": 1,
        "claimStatus": "native-c-abi-baseline-only",
        "contract": contract,
        "contractSha256": hashlib.sha256(_canonical_json(contract)).hexdigest(),
        "claimBoundary": CLAIM_BOUNDARY,
    }


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NativeAbiBaselineError(f"baseline JSON duplicates key {key!r}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise NativeAbiBaselineError(f"baseline JSON contains non-finite value {value}")


def _validate_json_unicode(value: Any) -> None:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str):
            try:
                item.encode("utf-8", errors="strict")
            except UnicodeEncodeError as error:
                raise NativeAbiBaselineError(
                    "native ABI baseline contains invalid Unicode"
                ) from error


def _load_baseline(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular(path, label="native ABI baseline", maximum=MAX_BASELINE_BYTES)
    try:
        source = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise NativeAbiBaselineError(
            "native ABI baseline must be strict UTF-8 JSON"
        ) from error
    if source.startswith("\ufeff"):
        raise NativeAbiBaselineError(
            "native ABI baseline must be strict UTF-8 JSON without a BOM"
        )
    try:
        value = json.loads(
            source,
            object_pairs_hook=_json_object,
            parse_constant=_invalid_constant,
        )
    except (json.JSONDecodeError, RecursionError) as error:
        raise NativeAbiBaselineError("native ABI baseline must be strict JSON") from error
    _validate_json_unicode(value)
    if not isinstance(value, dict):
        raise NativeAbiBaselineError("native ABI baseline must be one object")
    expected_keys = {
        "schemaVersion",
        "claimStatus",
        "contract",
        "contractSha256",
        "claimBoundary",
    }
    if set(value) != expected_keys:
        raise NativeAbiBaselineError("native ABI baseline has unexpected top-level keys")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise NativeAbiBaselineError("native ABI baseline schemaVersion must be 1")
    if value["claimStatus"] != "native-c-abi-baseline-only":
        raise NativeAbiBaselineError("native ABI baseline claimStatus is invalid")
    if value["claimBoundary"] != CLAIM_BOUNDARY:
        raise NativeAbiBaselineError("native ABI baseline claimBoundary is invalid")
    if not isinstance(value["contract"], dict):
        raise NativeAbiBaselineError("native ABI baseline contract must be an object")
    expected_digest = hashlib.sha256(_canonical_json(value["contract"])).hexdigest()
    if value["contractSha256"] != expected_digest:
        raise NativeAbiBaselineError("native ABI baseline contractSha256 is stale")
    if raw != _render_baseline(value):
        raise NativeAbiBaselineError(
            "native ABI baseline must use the canonical JSON serialization"
        )
    return value, raw


def verify_repository(repository: Path) -> dict[str, Any]:
    repository = repository.resolve(strict=True)
    baseline, _ = _load_baseline(repository / BASELINE_PATH)
    actual = build_baseline(repository)
    if baseline != actual:
        expected_lines = json.dumps(baseline, indent=2, sort_keys=True).splitlines()
        actual_lines = json.dumps(actual, indent=2, sort_keys=True).splitlines()
        diff = list(
            difflib.unified_diff(
                expected_lines,
                actual_lines,
                fromfile=BASELINE_PATH,
                tofile="current native C ABI",
                lineterm="",
            )
        )
        if len(diff) > MAX_DIFF_LINES:
            diff = diff[:MAX_DIFF_LINES] + ["... diff truncated ..."]
        raise NativeAbiBaselineError(
            "native C ABI differs from the committed baseline\n" + "\n".join(diff)
        )
    return actual


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Fonix repository root (defaults to this checkout)",
    )
    parser.add_argument(
        "--print-current",
        action="store_true",
        help="print the current canonical record instead of verifying it",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.print_current:
            sys.stdout.buffer.write(_render_baseline(build_baseline(arguments.repository)))
            return 0
        baseline = verify_repository(arguments.repository)
    except (NativeAbiBaselineError, OSError) as error:
        print(f"native C ABI baseline verification failed: {error}", file=sys.stderr)
        return 1
    contract = baseline["contract"]
    print(
        "Verified native C ABI baseline "
        f"sha256={baseline['contractSha256']} "
        f"constants={len(contract['abiConstants'])} "
        f"enums={len(contract['enums'])} "
        f"structs={len(contract['structs'])} "
        f"functions={len(contract['functions'])}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
