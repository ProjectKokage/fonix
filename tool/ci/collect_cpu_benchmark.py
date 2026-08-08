#!/usr/bin/env python3
"""Collect five fresh-process macOS/Linux CPU benchmark fragments.

This command is a host-side evidence collector, not a benchmark validator or a
performance claim.  It launches one already-built final application at a time,
binds every direct-child PID to a fresh challenge returned by the application,
and publishes the complete collection only after all five launches and every
post-run identity check pass.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import secrets
import stat
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Sequence


sys.dont_write_bytecode = True

_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(_DIRECTORY))

import bounded_process  # noqa: E402
import cpu_benchmark_collection  # noqa: E402


LAUNCH_COUNT = 5
BENCHMARK_PREFIX = "FONIX_CPU_BENCHMARK_FRAGMENT="
BENCHMARK_ACTIVATION = "FONIX_CPU_BENCHMARK"
BENCHMARK_CHALLENGE = "FONIX_CPU_BENCHMARK_CHALLENGE"
MACOS_RELEASE_BOOTSTRAP_STDERR = (
    "[IMPORTANT:flutter/shell/platform/embedder/"
    "embedder_surface_metal_impeller.mm(53)] Using the Impeller rendering "
    "backend (MetalSDF).\n"
)
COLLECTION_FILENAME = "cpu-benchmark-collection.json"
HOST_OBSERVATIONS_FILENAME = "host-observations.json"
FRAGMENT_FILENAME = "fragment-{index:02d}.json"
LAUNCH_PRIVATE_PREFIX = ".fonix-cpu-launch-"
COLLECTION_TIMEOUT_SECONDS = 10 * 60
MAX_FRAGMENT_BYTES = 128 * 1024
MAX_STDOUT_BYTES = len(BENCHMARK_PREFIX) + MAX_FRAGMENT_BYTES + 1
MAX_STDERR_BYTES = 16 * 1024
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024
MAX_OBSERVATION_BYTES = 1024 * 1024
MAX_PATH_BYTES = 4096
MAX_PROCESS_ID = (1 << 31) - 1
DISPLAY = re.compile(r"^:[0-9]{1,5}$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+() ,:=+-]{0,255}$")
LINUX_CPU_IDENTITY_FIELDS = frozenset(
    {
        "vendor_id",
        "cpu family",
        "model",
        "model name",
        "stepping",
        "microcode",
        "flags",
    }
)
HOST_OBSERVATION_CLAIM = (
    "Raw challenge-bound host observations captured around five fresh target "
    "processes. Collector-observed measurement input only; not a baseline, "
    "threshold, support claim, release approval, or independent hardware "
    "attestation."
)


class CpuBenchmarkCollectorError(RuntimeError):
    """The live host collection failed closed."""


class HostObservation(NamedTuple):
    device_identity_sha256: str
    os_version: str
    os_build: str
    driver_identity: str
    firmware_identity: str
    power_mode: str
    thermal_state: str


class UsageSnapshot(NamedTuple):
    user_seconds: float
    system_seconds: float


class DirectoryIdentity(NamedTuple):
    device: int
    inode: int


class OwnedDirectory(NamedTuple):
    name: str
    descriptor: int
    identity: DirectoryIdentity


Runner = Callable[..., Any]
HostIdentity = Callable[[], tuple[str, str]]
EnvironmentObserver = Callable[[str, str], HostObservation]
ChallengeFactory = Callable[[int], str]
UsageReader = Callable[[], UsageSnapshot]
ClockReader = Callable[[], int]


def _path(value: Path, label: str) -> Path:
    raw = os.fsencode(str(value))
    if (
        not value.is_absolute()
        or not raw
        or len(raw) > MAX_PATH_BYTES
        or b"\x00" in raw
        or any(byte < 0x20 for byte in raw)
    ):
        raise CpuBenchmarkCollectorError(f"{label} must be a bounded absolute path")
    return value


def _directory(path: Path, label: str) -> Path:
    _path(path, label)
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise CpuBenchmarkCollectorError(f"missing {label}") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise CpuBenchmarkCollectorError(f"{label} must be a non-link directory")
    return path


def _regular_file(path: Path, label: str, *, executable: bool = False) -> Path:
    _path(path, label)
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise CpuBenchmarkCollectorError(f"missing {label}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise CpuBenchmarkCollectorError(f"{label} must be a regular non-link file")
    if executable and not os.access(path, os.X_OK):
        raise CpuBenchmarkCollectorError(f"{label} must be executable")
    return path


def _resolved_leaf(path: Path, label: str) -> Path:
    """Resolve only a leaf's parent so lstat still rejects a leaf link."""

    _path(path, label)
    if path.name in {"", ".", ".."}:
        raise CpuBenchmarkCollectorError(f"{label} leaf name is invalid")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise CpuBenchmarkCollectorError(f"missing {label} parent") from error
    _directory(parent, f"{label} parent")
    return parent / path.name


def _ancestor_directory_identities(path: Path, label: str) -> set[tuple[int, int]]:
    identities: set[tuple[int, int]] = set()
    current = path
    while True:
        try:
            metadata = current.lstat()
        except OSError as error:
            raise CpuBenchmarkCollectorError(
                f"could not inspect {label} ancestry"
            ) from error
        if not stat.S_ISDIR(metadata.st_mode):
            raise CpuBenchmarkCollectorError(f"{label} ancestry is not canonical")
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in identities:
            break
        identities.add(identity)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return identities


def _reject_output_overlap(
    output: Path,
    parent: Path,
    protected_roots: Sequence[tuple[Path, str]],
) -> None:
    output_parent_ancestors = _ancestor_directory_identities(
        parent,
        "output parent",
    )
    resolved_output: Path | None = None
    if output.exists() or output.is_symlink():
        try:
            resolved_output = output.resolve(strict=True)
        except OSError:
            resolved_output = None
    for protected_root, label in protected_roots:
        try:
            protected_status = protected_root.lstat()
        except OSError as error:
            raise CpuBenchmarkCollectorError(
                f"could not inspect {label} identity"
            ) from error
        protected_identity = (protected_status.st_dev, protected_status.st_ino)
        if protected_identity in output_parent_ancestors:
            raise CpuBenchmarkCollectorError(
                f"output directory must not be inside or equal to the {label}"
            )
        if resolved_output is None:
            continue
        target_parent = (
            resolved_output
            if resolved_output.is_dir()
            else resolved_output.parent
        )
        target_ancestors = _ancestor_directory_identities(
            target_parent,
            "resolved output",
        )
        if protected_identity in target_ancestors:
            raise CpuBenchmarkCollectorError(
                f"output directory alias must not resolve inside the {label}"
            )
        protected_ancestors = _ancestor_directory_identities(
            protected_root,
            label,
        )
        try:
            output_status = resolved_output.lstat()
        except OSError:
            continue
        if (output_status.st_dev, output_status.st_ino) in protected_ancestors:
            raise CpuBenchmarkCollectorError(
                f"output directory must not contain or equal the {label}"
            )


def _logical_id(path: Path, label: str) -> str:
    value = path.name
    if LABEL.fullmatch(value) is None:
        raise CpuBenchmarkCollectorError(
            f"{label} basename must be a bounded path-free label"
        )
    return value


def _host_identity() -> tuple[str, str]:
    system = platform.system()
    architecture = platform.machine()
    if system == "Darwin" and architecture == "arm64":
        return "macos", "arm64"
    if system == "Linux" and architecture == "x86_64":
        return "linux", "x86_64"
    raise CpuBenchmarkCollectorError(
        "the CPU collector requires macOS arm64 or Linux x86_64"
    )


def _read_small_file(
    path: Path,
    *,
    maximum: int = MAX_OBSERVATION_BYTES,
) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        descriptor = os.open(path, flags)
    except (FileNotFoundError, PermissionError, OSError):
        return None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            return None
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        len(data) > maximum
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        return None
    return data


def _path_free_label(value: str, *, fallback_source: bytes) -> str:
    if (
        LABEL.fullmatch(value) is not None
        and value == value.strip()
        and "/" not in value
        and "\\" not in value
        and "://" not in value
    ):
        return value
    return "sha256-" + hashlib.sha256(fallback_source).hexdigest()


def _linux_power_mode() -> str:
    governors: set[str] = set()
    roots = sorted(
        Path("/sys/devices/system/cpu").glob(
            "cpu[0-9]*/cpufreq/scaling_governor"
        )
    )
    if len(roots) > 4096:
        return "not-exposed-by-host-api"
    for path in roots:
        data = _read_small_file(path, maximum=256)
        if data is None:
            continue
        try:
            value = data.decode("ascii").strip()
        except UnicodeDecodeError:
            continue
        if TOKEN.fullmatch(value) is not None:
            governors.add(value)
    if not governors:
        return "not-exposed-by-host-api"
    if len(governors) == 1:
        return f"cpu-governor-{next(iter(governors))}"
    digest = hashlib.sha256("\n".join(sorted(governors)).encode("ascii")).hexdigest()
    return f"cpu-governor-mixed-{digest[:32]}"


def _linux_cpu_identity_source() -> bytes:
    """Return stable CPU identity bytes without volatile /proc counters."""

    fields: dict[str, str] = {}
    raw = _read_small_file(Path("/proc/cpuinfo"))
    if raw is not None:
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if not line.strip() and fields:
                break
            key, separator, value = line.partition(":")
            key = key.strip()
            if separator and key in LINUX_CPU_IDENTITY_FIELDS and key not in fields:
                fields[key] = " ".join(value.split())
    identity = {
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "cpuInfo": fields,
    }
    return json.dumps(
        identity, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _macos_platform_identifier_sha256() -> str:
    """Hash one stable platform UUID obtained from the trusted ioreg tool."""

    tool = _regular_file(
        Path("/usr/sbin/ioreg"), "macOS platform identity tool", executable=True
    )
    try:
        output = bounded_process.run_bounded(
            (str(tool), "-rd1", "-c", "IOPlatformExpertDevice"),
            operation="macOS platform identity",
            cwd=Path("/"),
            environment={
                "PATH": "/usr/bin:/bin:/usr/sbin",
                "LC_ALL": "C",
                "LANG": "C",
            },
            timeout_seconds=10,
            maximum_stdout_bytes=256 * 1024,
            maximum_stderr_bytes=4 * 1024,
        )
    except Exception as error:
        raise CpuBenchmarkCollectorError(
            "macOS platform identity could not be observed safely"
        ) from error
    if output.stderr != "":
        raise CpuBenchmarkCollectorError(
            "macOS platform identity tool emitted unexpected diagnostics"
        )
    matches = re.findall(
        r'(?m)^\s*"IOPlatformUUID"\s*=\s*"'
        r'([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-'
        r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})"\s*$',
        output.stdout,
    )
    if len(matches) != 1:
        raise CpuBenchmarkCollectorError(
            "macOS platform identity tool returned no unique platform UUID"
        )
    normalized = matches[0].lower()
    if set(normalized.replace("-", "")) == {"0"}:
        raise CpuBenchmarkCollectorError("macOS platform UUID is invalid")
    return hashlib.sha256(
        b"fonix-macos-platform-uuid-v1\x00" + normalized.encode("ascii")
    ).hexdigest()


def _observe_host_environment(platform_id: str, architecture: str) -> HostObservation:
    if (platform_id, architecture) not in {
        ("macos", "arm64"),
        ("linux", "x86_64"),
    }:
        raise CpuBenchmarkCollectorError("host observation tuple is unsupported")

    if platform_id == "macos":
        private_identity = {
            "platform": platform_id,
            "architecture": architecture,
            "platformIdentifierSha256": _macos_platform_identifier_sha256(),
        }
    else:
        machine_id = _read_small_file(Path("/etc/machine-id"), maximum=4096)
        private_identity = {
            "platform": platform_id,
            "architecture": architecture,
            "node": platform.node(),
            "machineId": None if machine_id is None else machine_id.hex(),
        }
    device_identity = hashlib.sha256(
        json.dumps(
            private_identity, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
    ).hexdigest()

    if platform_id == "macos":
        os_version_raw = platform.mac_ver()[0] or platform.release()
        power_mode = "not-exposed-by-host-api"
        cpu_source = platform.processor().encode("utf-8", errors="replace")
        firmware_source = None
    else:
        os_version_raw = platform.release()
        power_mode = _linux_power_mode()
        cpu_source = _linux_cpu_identity_source()
        firmware_parts = [
            value
            for value in (
                _read_small_file(Path("/sys/class/dmi/id/bios_vendor"), maximum=4096),
                _read_small_file(Path("/sys/class/dmi/id/bios_version"), maximum=4096),
                _read_small_file(Path("/sys/class/dmi/id/bios_date"), maximum=4096),
            )
            if value is not None
        ]
        firmware_source = b"\x00".join(firmware_parts) if firmware_parts else None

    version_source = os_version_raw.encode("utf-8", errors="replace")
    build_source = platform.version().encode("utf-8", errors="replace")
    return HostObservation(
        device_identity_sha256=device_identity,
        os_version=_path_free_label(os_version_raw, fallback_source=version_source),
        os_build=_path_free_label(
            platform.version(), fallback_source=build_source
        ),
        driver_identity="sha256-" + hashlib.sha256(cpu_source).hexdigest(),
        firmware_identity=(
            "not-exposed-by-host-api"
            if firmware_source is None
            else "sha256-" + hashlib.sha256(firmware_source).hexdigest()
        ),
        power_mode=power_mode,
        thermal_state="not-exposed-by-host-api",
    )


def _launch_environment(
    root: Path,
    *,
    challenge: str,
    platform_id: str,
    display: str | None,
) -> dict[str, str]:
    if re.fullmatch(r"[0-9a-f]{64}", challenge) is None:
        raise CpuBenchmarkCollectorError("launch challenge is invalid")
    _directory(root, "launch private root")
    directories = {
        "home": root / "home",
        "tmp": root / "tmp",
        "config": root / "xdg-config",
        "cache": root / "xdg-cache",
        "data": root / "xdg-data",
        "state": root / "xdg-state",
        "runtime": root / "xdg-runtime",
        "cwd": root / "cwd",
    }
    for directory in directories.values():
        directory.mkdir(mode=0o700)
    environment = {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "LANG": "C",
        "HOME": str(directories["home"]),
        "TMPDIR": str(directories["tmp"]),
        "TMP": str(directories["tmp"]),
        "TEMP": str(directories["tmp"]),
        "XDG_CONFIG_HOME": str(directories["config"]),
        "XDG_CACHE_HOME": str(directories["cache"]),
        "XDG_DATA_HOME": str(directories["data"]),
        "XDG_STATE_HOME": str(directories["state"]),
        "XDG_RUNTIME_DIR": str(directories["runtime"]),
        BENCHMARK_ACTIVATION: "1",
        BENCHMARK_CHALLENGE: challenge,
    }
    if platform_id == "linux":
        if display is None or DISPLAY.fullmatch(display) is None:
            raise CpuBenchmarkCollectorError(
                "Linux collection requires a valid display"
            )
        environment.update(
            {
                "DISPLAY": display,
                "GSETTINGS_BACKEND": "memory",
                "MESA_GLSL_CACHE_DISABLE": "true",
                "MESA_SHADER_CACHE_DISABLE": "true",
                "NO_AT_BRIDGE": "1",
            }
        )
    elif display is not None:
        raise CpuBenchmarkCollectorError("--display is Linux-only")
    return environment


def _usage() -> UsageSnapshot:
    value = resource.getrusage(resource.RUSAGE_CHILDREN)
    return UsageSnapshot(value.ru_utime, value.ru_stime)


def _usage_delta(
    before: UsageSnapshot,
    after: UsageSnapshot,
    label: str,
) -> tuple[int, int]:
    user = round((after.user_seconds - before.user_seconds) * 1_000_000)
    system = round((after.system_seconds - before.system_seconds) * 1_000_000)
    if user < 0 or system < 0:
        raise CpuBenchmarkCollectorError(f"{label} child CPU accounting regressed")
    return user, system


def _parse_fragment_output(
    stdout: str,
    stderr: str,
    *,
    platform_id: str,
    challenge: str,
    process_id: int,
    core: Any,
) -> tuple[dict[str, Any], bytes, str]:
    if platform_id == "macos":
        expected_stderr = MACOS_RELEASE_BOOTSTRAP_STDERR
    elif platform_id == "linux":
        expected_stderr = ""
    else:
        raise CpuBenchmarkCollectorError(
            "benchmark stderr contract platform is unsupported"
        )
    if stderr != expected_stderr:
        raise CpuBenchmarkCollectorError(
            "benchmark application stderr does not match its closed platform contract"
        )
    try:
        encoded = stdout.encode("utf-8")
    except UnicodeEncodeError as error:  # pragma: no cover - runner is strict UTF-8
        raise CpuBenchmarkCollectorError("benchmark stdout is not UTF-8") from error
    if (
        len(encoded) > MAX_STDOUT_BYTES
        or "\r" in stdout
        or "\x00" in stdout
        or stdout.count("\n") != 1
        or not stdout.endswith("\n")
        or not stdout.startswith(BENCHMARK_PREFIX)
    ):
        raise CpuBenchmarkCollectorError(
            "benchmark application must emit exactly one bounded fragment line"
        )
    payload = stdout[len(BENCHMARK_PREFIX) : -1].encode("utf-8")
    if not payload or len(payload) > MAX_FRAGMENT_BYTES:
        raise CpuBenchmarkCollectorError("benchmark fragment is outside its byte bound")
    digest = hashlib.sha256(payload).hexdigest()
    value = core.strict_json_loads(
        payload,
        label="CPU benchmark fragment",
        maximum_bytes=MAX_FRAGMENT_BYTES,
    )
    normalized = core.validate_fragment(
        value,
        expected_challenge=challenge,
        expected_process_id=process_id,
        raw_sha256=digest,
    )
    return normalized, payload, digest


def _application_executable(
    application_root: Path,
    executable: Path,
) -> tuple[Path, Path]:
    application_root = _directory(
        _resolved_leaf(application_root, "application root"), "application root"
    )
    executable = _regular_file(
        _resolved_leaf(executable, "application executable"),
        "application executable",
        executable=True,
    )
    try:
        relative = executable.relative_to(application_root)
    except ValueError as error:
        raise CpuBenchmarkCollectorError(
            "application executable must be inside the application root"
        ) from error
    if not relative.parts:
        raise CpuBenchmarkCollectorError("application executable descendant is invalid")
    return application_root, executable


def _application_member_identity(
    application_root: Path,
    path: Path,
    tree: Mapping[str, Any],
    *,
    label: str,
    core: Any,
) -> dict[str, Any]:
    try:
        relative = path.relative_to(application_root).as_posix()
    except ValueError as error:
        raise CpuBenchmarkCollectorError(
            f"{label} must be inside the measured application tree"
        ) from error
    if not relative or relative in {".", ".."}:
        raise CpuBenchmarkCollectorError(f"{label} application member is invalid")
    entries = tree.get("_entries")
    if not isinstance(entries, list):
        raise CpuBenchmarkCollectorError(
            "application tree did not expose its private member inventory"
        )
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("path") == relative
    ]
    if len(matches) != 1 or matches[0].get("type") != "file":
        raise CpuBenchmarkCollectorError(
            f"{label} is not one exact regular-file member of the application tree"
        )
    recorded = {
        "sizeBytes": matches[0].get("sizeBytes"),
        "sha256": matches[0].get("sha256"),
    }
    current = core.regular_file_identity(
        path,
        label=label,
        maximum_bytes=MAX_ARTIFACT_BYTES,
    )
    if recorded != current:
        raise CpuBenchmarkCollectorError(
            f"{label} identity differs from the measured application tree"
        )
    return {"id": _logical_id(path, label), **current}


def _require_unique_packaged_basenames(
    tree: Mapping[str, Any],
    supplied: Sequence[tuple[Path, str]],
) -> None:
    entries = tree.get("_entries")
    if not isinstance(entries, list):
        raise CpuBenchmarkCollectorError(
            "application tree did not expose its private member inventory"
        )
    regular_basenames: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") != "file":
            continue
        relative = entry.get("path")
        if not isinstance(relative, str) or not relative:
            raise CpuBenchmarkCollectorError(
                "application tree contains an invalid regular-file path"
            )
        basename = relative.rsplit("/", 1)[-1]
        regular_basenames[basename] = regular_basenames.get(basename, 0) + 1
    supplied_basenames: set[str] = set()
    for path, label in supplied:
        basename = _logical_id(path, label)
        if basename in supplied_basenames:
            raise CpuBenchmarkCollectorError(
                "supplied packaged artifact basenames must be distinct"
            )
        supplied_basenames.add(basename)
        if regular_basenames.get(basename) != 1:
            raise CpuBenchmarkCollectorError(
                f"{label} basename must select exactly one regular-file member "
                "of the application tree"
            )


def _provider_identities(
    paths: Sequence[Path],
    *,
    application_root: Path,
    tree: Mapping[str, Any],
    core: Any,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    observed: set[str] = set()
    for path in paths:
        logical_id = _logical_id(path, "provider dependency")
        if logical_id in observed:
            raise CpuBenchmarkCollectorError("provider dependency IDs must be unique")
        observed.add(logical_id)
        identity = _application_member_identity(
            application_root,
            path,
            tree,
            label=f"provider dependency {logical_id}",
            core=core,
        )
        if identity["id"] != logical_id:
            raise CpuBenchmarkCollectorError(
                "provider dependency logical identity changed"
            )
        result.append(identity)
    result.sort(key=lambda value: value["id"])
    return result


def _artifact_snapshot(
    *,
    repository: Path,
    application_root: Path,
    executable: Path,
    shim_artifact: Path,
    runtime_artifact: Path,
    resolver_manifest: Path,
    provider_dependencies: Sequence[Path],
    core: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tree = core.canonical_application_tree_identity(
        application_root,
        label="application",
        include_entries=True,
    )
    tree_fields = {
        key: tree[key]
        for key in (
            "format",
            "fileCount",
            "directoryCount",
            "symbolicLinkCount",
            "byteCount",
            "sha256",
        )
    }
    _require_unique_packaged_basenames(
        tree,
        (
            (executable, "application executable"),
            (shim_artifact, "shim artifact"),
            (runtime_artifact, "runtime artifact"),
            (resolver_manifest, "resolver manifest"),
            *(
                (
                    path,
                    f"provider dependency {_logical_id(path, 'provider dependency')}",
                )
                for path in provider_dependencies
            ),
        ),
    )
    executable_identity = _application_member_identity(
        application_root,
        executable,
        tree,
        label="application executable",
        core=core,
    )
    runtime_identity = _application_member_identity(
        application_root,
        runtime_artifact,
        tree,
        label="runtime artifact",
        core=core,
    )
    shim_identity = _application_member_identity(
        application_root,
        shim_artifact,
        tree,
        label="shim artifact",
        core=core,
    )
    resolver_identity = _application_member_identity(
        application_root,
        resolver_manifest,
        tree,
        label="resolver manifest",
        core=core,
    )
    binding = core.validate_native_build_binding(
        repository=repository,
        shim_binary=shim_artifact,
        resolver_manifest=resolver_manifest,
    )
    if binding["shimLibrary"] != {
        "sizeBytes": shim_identity["sizeBytes"],
        "sha256": shim_identity["sha256"],
    }:
        raise CpuBenchmarkCollectorError(
            "native build binding contradicts the supplied shim artifact"
        )
    if binding["resolverManifest"] != {
        "sizeBytes": resolver_identity["sizeBytes"],
        "sha256": resolver_identity["sha256"],
    }:
        raise CpuBenchmarkCollectorError(
            "native build binding contradicts the packaged resolver manifest"
        )
    repository_evidence = core.repository_evidence_identity(repository)
    providers = _provider_identities(
        provider_dependencies,
        application_root=application_root,
        tree=tree,
        core=core,
    )
    artifacts = {
        "applicationTree": tree_fields,
        "executable": executable_identity,
        "shimLibrary": shim_identity,
        "runtimeLibrary": runtime_identity,
        "providerDependencies": providers,
        "nativeLock": binding["nativeLock"],
        "packageVersion": binding["packageVersion"],
        "runtimeVersion": binding["runtimeVersion"],
        "resolverManifest": resolver_identity,
        "embeddedBuildManifestSha256": binding["embeddedBuildManifestSha256"],
        "embeddedBuildManifestCanonicalSha256": binding[
            "embeddedBuildManifestCanonicalSha256"
        ],
        "embeddedBuildManifest": binding["buildManifest"],
        "nativePayloadFiles": binding["nativePayloadFiles"],
        "nativePayloadRelationship": (
            "lock-resolver-source-bytes;packaged-equivalence-requires-platform-audit"
        ),
        "repositoryEvidence": repository_evidence,
    }
    return artifacts, {"tree": tree, "binding": binding}


def _trusted_tool_identities(core: Any) -> dict[str, dict[str, Any]]:
    core_file = getattr(core, "__file__", None)
    if not isinstance(core_file, (str, os.PathLike)):
        raise CpuBenchmarkCollectorError(
            "CPU benchmark collection core has no stable source identity"
        )
    try:
        collector_path = Path(__file__).resolve(strict=True)
        core_path = Path(core_file).resolve(strict=True)
    except (OSError, TypeError) as error:
        raise CpuBenchmarkCollectorError(
            "trusted CPU benchmark tool source is unavailable"
        ) from error
    return {
        "collector": core.regular_file_identity(
            collector_path,
            label="CPU benchmark collector",
            maximum_bytes=MAX_OBSERVATION_BYTES,
        ),
        "core": core.regular_file_identity(
            core_path,
            label="CPU benchmark collection core",
            maximum_bytes=MAX_OBSERVATION_BYTES,
        ),
    }


def _environment_record(
    *,
    platform_id: str,
    architecture: str,
    launch_observations: Sequence[
        tuple[int, str, int, HostObservation, HostObservation]
    ],
    cpu_launches: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if len(launch_observations) != LAUNCH_COUNT:
        raise CpuBenchmarkCollectorError("host observation count is incomplete")
    first = launch_observations[0][3]
    stable = first[:5]
    for _, _, _, start, end in launch_observations:
        if start[:5] != stable or end[:5] != stable:
            raise CpuBenchmarkCollectorError(
                "stable host environment identity changed during collection"
            )
    observations = [
        {
            "index": index,
            "launchChallenge": challenge,
            "processId": process_id,
            "powerModeStart": start.power_mode,
            "powerModeEnd": end.power_mode,
            "thermalStateStart": start.thermal_state,
            "thermalStateEnd": end.thermal_state,
        }
        for index, challenge, process_id, start, end in launch_observations
    ]

    def summarize(values: Sequence[str], *, thermal: bool) -> dict[str, Any]:
        unavailable = "not-exposed-by-host-api"
        known = [value for value in values if value != unavailable]
        availability = (
            "unavailable"
            if not known
            else "available"
            if len(known) == len(values)
            else "partial"
        )
        changed = len(set(known)) > 1
        state_key = "drift" if thermal else "stability"
        if changed:
            state = "observed" if thermal else "changed"
        elif availability == "available":
            state = "none-observed" if thermal else "stable"
        else:
            state = "indeterminate"
        return {
            "availability": availability,
            state_key: state,
            "stableValue": known[0]
            if availability == "available" and not changed
            else None,
        }

    power_values = [
        value
        for observation in observations
        for value in (
            observation["powerModeStart"],
            observation["powerModeEnd"],
        )
    ]
    thermal_values = [
        value
        for observation in observations
        for value in (
            observation["thermalStateStart"],
            observation["thermalStateEnd"],
        )
    ]
    power = summarize(power_values, thermal=False)
    thermal = summarize(thermal_values, thermal=True)
    reasons: list[str] = []
    if power["availability"] == "unavailable":
        reasons.append("power-mode-unavailable")
    elif power["availability"] == "partial":
        reasons.append("power-mode-partially-unavailable")
    if power["stability"] == "changed":
        reasons.append("power-mode-changed")
    if thermal["availability"] == "unavailable":
        reasons.append("thermal-state-unavailable")
    elif thermal["availability"] == "partial":
        reasons.append("thermal-state-partially-unavailable")
    if thermal["drift"] == "observed":
        reasons.append("thermal-drift-observed")
    changed = power["stability"] == "changed" or thermal["drift"] == "observed"
    incomplete = (
        power["availability"] != "available"
        or thermal["availability"] != "available"
    )
    comparability = {
        "status": "non-comparable"
        if changed
        else "incomplete"
        if incomplete
        else "baseline-comparable",
        "powerMode": power,
        "thermalState": thermal,
        "reasons": reasons,
    }
    return {
        "platform": platform_id,
        "architecture": architecture,
        "deviceIdentitySha256": first.device_identity_sha256,
        "osVersion": first.os_version,
        "osBuild": first.os_build,
        "driverIdentity": first.driver_identity,
        "firmwareIdentity": first.firmware_identity,
        "launchObservations": observations,
        "comparability": comparability,
        "cpuUtilization": {
            "status": "measured",
            "launches": [dict(value) for value in cpu_launches],
        },
    }


def _host_observation_payload(environment: Mapping[str, Any]) -> bytes:
    record = {
        "schemaVersion": 1,
        "result": "measured",
        "claimStatus": "measurement-only",
        "purpose": "cpu-benchmark-host-observations",
        "launchCount": LAUNCH_COUNT,
        "environment": dict(environment),
        "claimBoundary": HOST_OBSERVATION_CLAIM,
    }
    try:
        payload = json.dumps(
            record,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise CpuBenchmarkCollectorError(
            "host observation record is not canonical JSON data"
        ) from error
    if not payload or len(payload) > MAX_OBSERVATION_BYTES:
        raise CpuBenchmarkCollectorError(
            "host observation record exceeds its byte bound"
        )
    return payload


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _leaf_name(value: str, label: str) -> str:
    raw = os.fsencode(value)
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or not raw
        or len(raw) > 255
        or b"\x00" in raw
        or any(byte < 0x20 for byte in raw)
    ):
        raise CpuBenchmarkCollectorError(f"{label} name is invalid")
    return value


def _identity(metadata: os.stat_result, label: str) -> DirectoryIdentity:
    if not stat.S_ISDIR(metadata.st_mode):
        raise CpuBenchmarkCollectorError(f"{label} is not a directory")
    return DirectoryIdentity(metadata.st_dev, metadata.st_ino)


def _verify_descriptor(
    descriptor: int,
    identity: DirectoryIdentity,
    label: str,
) -> os.stat_result:
    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise CpuBenchmarkCollectorError(
            f"{label} descriptor is unavailable"
        ) from error
    if _identity(metadata, label) != identity:
        raise CpuBenchmarkCollectorError(f"{label} descriptor identity changed")
    return metadata


def _open_owned_parent(path: Path, label: str) -> tuple[int, DirectoryIdentity]:
    expected = _identity(path.lstat(), label)
    try:
        descriptor = os.open(path, _directory_open_flags())
    except OSError as error:
        raise CpuBenchmarkCollectorError(f"could not retain {label}") from error
    try:
        identity = _identity(os.fstat(descriptor), label)
        if identity != expected:
            raise CpuBenchmarkCollectorError(f"{label} changed while opening")
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        raise


def _verify_canonical_directory_binding(
    path: Path,
    descriptor: int,
    identity: DirectoryIdentity,
    label: str,
) -> None:
    _verify_descriptor(descriptor, identity, label)
    try:
        resolved = path.resolve(strict=True)
        named = path.lstat()
    except OSError as error:
        raise CpuBenchmarkCollectorError(f"{label} path is unavailable") from error
    if resolved != path or _identity(named, label) != identity:
        raise CpuBenchmarkCollectorError(f"{label} path identity changed")
    _verify_descriptor(descriptor, identity, label)


def _verify_output_confinement(
    *,
    output: Path,
    parent: Path,
    parent_descriptor: int,
    parent_identity: DirectoryIdentity,
    protected_roots: Sequence[tuple[Path, str]],
) -> None:
    _verify_canonical_directory_binding(
        parent,
        parent_descriptor,
        parent_identity,
        "output parent",
    )
    _reject_output_overlap(output, parent, protected_roots)
    _verify_canonical_directory_binding(
        parent,
        parent_descriptor,
        parent_identity,
        "output parent",
    )


def _create_owned_directory(
    parent_descriptor: int,
    parent_identity: DirectoryIdentity,
    name: str,
    label: str,
) -> OwnedDirectory:
    name = _leaf_name(name, label)
    _verify_descriptor(parent_descriptor, parent_identity, f"{label} parent")
    try:
        os.mkdir(name, 0o700, dir_fd=parent_descriptor)
    except OSError as error:
        if error.errno == errno.EEXIST:
            raise CpuBenchmarkCollectorError(f"{label} already exists") from error
        raise CpuBenchmarkCollectorError(f"could not create {label}") from error
    descriptor: int | None = None
    owner: OwnedDirectory | None = None
    try:
        created = _identity(
            os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False),
            label,
        )
        descriptor = os.open(
            name,
            _directory_open_flags(),
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
        identity = _identity(metadata, label)
        owner = OwnedDirectory(name, descriptor, identity)
        named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            created != identity
            or _identity(named, label) != identity
        ):
            raise CpuBenchmarkCollectorError(f"{label} identity changed while opening")
        os.fchmod(descriptor, 0o700)
        metadata = _verify_descriptor(descriptor, identity, label)
        if (
            stat.S_IMODE(metadata.st_mode) != 0o700
            or not _named_directory_matches(parent_descriptor, owner)
        ):
            raise CpuBenchmarkCollectorError(f"{label} identity changed while opening")
        return owner
    except BaseException:
        if owner is not None and _named_directory_matches(parent_descriptor, owner):
            try:
                os.rmdir(name, dir_fd=parent_descriptor)
            except OSError:
                pass
        if descriptor is not None:
            os.close(descriptor)
        raise


def _named_directory_matches(
    parent_descriptor: int,
    owner: OwnedDirectory,
) -> bool:
    try:
        metadata = os.stat(
            owner.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and (metadata.st_dev, metadata.st_ino)
        == (owner.identity.device, owner.identity.inode)
    )


def _clear_owned_directory(descriptor: int) -> bool:
    """Empty one retained directory without following a replaced path."""

    try:
        names = os.listdir(descriptor)
    except OSError:
        return False
    succeeded = True
    for name in names:
        try:
            before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError:
            succeeded = False
            continue
        if stat.S_ISDIR(before.st_mode):
            child_descriptor: int | None = None
            try:
                child_descriptor = os.open(
                    name,
                    _directory_open_flags(),
                    dir_fd=descriptor,
                )
                opened = os.fstat(child_descriptor)
                if (before.st_dev, before.st_ino) != (
                    opened.st_dev,
                    opened.st_ino,
                ):
                    succeeded = False
                    continue
                if not _clear_owned_directory(child_descriptor):
                    succeeded = False
                    continue
                current = os.stat(
                    name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISDIR(current.st_mode)
                    or (current.st_dev, current.st_ino)
                    != (opened.st_dev, opened.st_ino)
                ):
                    succeeded = False
                    continue
                os.rmdir(name, dir_fd=descriptor)
            except OSError:
                succeeded = False
            finally:
                if child_descriptor is not None:
                    os.close(child_descriptor)
        else:
            try:
                os.unlink(name, dir_fd=descriptor)
            except OSError:
                succeeded = False
    try:
        if os.listdir(descriptor):
            succeeded = False
    except OSError:
        succeeded = False
    return succeeded


def _cleanup_owned_directory(
    parent_descriptor: int,
    parent_identity: DirectoryIdentity,
    owner: OwnedDirectory,
) -> bool:
    """Remove only the retained inode; never recurse through its current path."""

    try:
        _verify_descriptor(owner.descriptor, owner.identity, "owned directory")
        emptied = _clear_owned_directory(owner.descriptor)
        _verify_descriptor(parent_descriptor, parent_identity, "owned directory parent")
    except (CpuBenchmarkCollectorError, OSError):
        return False
    if not emptied or not _named_directory_matches(parent_descriptor, owner):
        return False
    try:
        os.rmdir(owner.name, dir_fd=parent_descriptor)
    except OSError:
        return False
    return True


def _write_new_file(directory_descriptor: int, name: str, data: bytes) -> None:
    name = _leaf_name(name, "output file")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=directory_descriptor)
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise CpuBenchmarkCollectorError("could not write collection output")
            remaining = remaining[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != len(data)
        ):
            raise CpuBenchmarkCollectorError("collection output changed while writing")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_exact_file(
    directory_descriptor: int,
    name: str,
    expected: bytes,
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != len(expected)
        ):
            raise CpuBenchmarkCollectorError("published output metadata changed")
        chunks: list[bytes] = []
        remaining = len(expected) + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        observed = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            observed != expected
            or (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
            )
        ):
            raise CpuBenchmarkCollectorError("published output content changed")
    except OSError as error:
        raise CpuBenchmarkCollectorError(
            "published output cannot be inspected"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _verify_output_inventory(
    output: OwnedDirectory,
    expected: Mapping[str, bytes],
) -> None:
    metadata = _verify_descriptor(
        output.descriptor,
        output.identity,
        "output directory",
    )
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise CpuBenchmarkCollectorError("output directory mode changed")
    try:
        observed = os.listdir(output.descriptor)
    except OSError as error:
        raise CpuBenchmarkCollectorError(
            "publication inventory is unavailable"
        ) from error
    if len(observed) != len(set(observed)) or set(observed) != set(expected):
        raise CpuBenchmarkCollectorError("publication output inventory is not exact")
    for name, payload in expected.items():
        _read_exact_file(output.descriptor, name, payload)


def _publish(
    *,
    parent_descriptor: int,
    parent_identity: DirectoryIdentity,
    output: OwnedDirectory,
    fragment_payloads: Sequence[bytes],
    host_observation_payload: bytes,
    collection: Mapping[str, Any],
) -> None:
    encoded_collection = (
        json.dumps(collection, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    expected = {
        **{
            FRAGMENT_FILENAME.format(index=index): payload
            for index, payload in enumerate(fragment_payloads)
        },
        HOST_OBSERVATIONS_FILENAME: host_observation_payload,
        COLLECTION_FILENAME: encoded_collection,
    }
    try:
        _verify_descriptor(parent_descriptor, parent_identity, "output parent")
        if not _named_directory_matches(parent_descriptor, output):
            raise CpuBenchmarkCollectorError("output directory path was replaced")
        for name, payload in expected.items():
            _write_new_file(output.descriptor, name, payload)
        _verify_output_inventory(output, expected)
        os.fsync(output.descriptor)
        _verify_descriptor(parent_descriptor, parent_identity, "output parent")
        if not _named_directory_matches(parent_descriptor, output):
            raise CpuBenchmarkCollectorError("output directory path was replaced")
        os.fsync(parent_descriptor)
        _verify_descriptor(parent_descriptor, parent_identity, "output parent")
        if not _named_directory_matches(parent_descriptor, output):
            raise CpuBenchmarkCollectorError("output directory path was replaced")
        _verify_output_inventory(output, expected)
        _verify_descriptor(parent_descriptor, parent_identity, "output parent")
        if not _named_directory_matches(parent_descriptor, output):
            raise CpuBenchmarkCollectorError("output directory path was replaced")
    except CpuBenchmarkCollectorError:
        raise
    except OSError as error:
        raise CpuBenchmarkCollectorError(
            "output publication durability could not be confirmed"
        ) from error


def collect(
    arguments: argparse.Namespace,
    *,
    core: Any = cpu_benchmark_collection,
    runner: Runner = bounded_process.run_bounded,
    host_identity: HostIdentity = _host_identity,
    environment_observer: EnvironmentObserver = _observe_host_environment,
    launch_environment: Callable[..., dict[str, str]] = _launch_environment,
    challenge_factory: ChallengeFactory = lambda _: secrets.token_hex(32),
    usage_reader: UsageReader = _usage,
    clock_reader: ClockReader = time.monotonic_ns,
) -> dict[str, Any]:
    platform_id, architecture = host_identity()
    if (platform_id, architecture) not in {
        ("macos", "arm64"),
        ("linux", "x86_64"),
    }:
        raise CpuBenchmarkCollectorError(
            "host identity hook returned an unsupported tuple"
        )
    display = arguments.display
    if platform_id == "linux":
        if display is None or DISPLAY.fullmatch(display) is None:
            raise CpuBenchmarkCollectorError("Linux collection requires --display")
    elif display is not None:
        raise CpuBenchmarkCollectorError("--display is Linux-only")

    repository = _directory(
        _resolved_leaf(arguments.repository, "repository"), "repository"
    )
    _path(arguments.application_root, "application root")
    _path(arguments.executable, "application executable")
    _path(arguments.shim_artifact, "shim artifact")
    _path(arguments.runtime_artifact, "runtime artifact")
    _path(arguments.resolver_manifest, "resolver manifest")
    for dependency in arguments.provider_dependency:
        _path(dependency, "provider dependency")
    application_root, executable = _application_executable(
        arguments.application_root, arguments.executable
    )
    shim_artifact = _regular_file(
        _resolved_leaf(arguments.shim_artifact, "shim artifact"), "shim artifact"
    )
    runtime_artifact = _regular_file(
        _resolved_leaf(arguments.runtime_artifact, "runtime artifact"),
        "runtime artifact",
    )
    resolver_manifest = _regular_file(
        _resolved_leaf(arguments.resolver_manifest, "resolver manifest"),
        "resolver manifest",
    )
    provider_dependencies = tuple(
        _regular_file(
            _resolved_leaf(path, "provider dependency"), "provider dependency"
        )
        for path in arguments.provider_dependency
    )

    output = _path(arguments.output_directory, "output directory")
    if output.name in {"", ".", ".."}:
        raise CpuBenchmarkCollectorError("output directory name is invalid")
    parent = _directory(output.parent.resolve(strict=True), "output parent")
    output = parent / output.name
    output_name = _leaf_name(output.name, "output directory")
    _reject_output_overlap(
        output,
        parent,
        (
            (application_root, "application tree"),
            (repository, "repository"),
        ),
    )
    parent_descriptor, parent_identity = _open_owned_parent(parent, "output parent")
    launch_parent = _directory(
        Path(tempfile.gettempdir()).resolve(strict=True),
        "launch-private parent",
    )
    launch_parent_descriptor: int | None = None
    output_owner: OwnedDirectory | None = None
    try:
        try:
            os.stat(
                output_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise CpuBenchmarkCollectorError(
                "output directory must not already exist"
            )
        launch_parent_descriptor, launch_parent_identity = _open_owned_parent(
            launch_parent,
            "launch-private parent",
        )
        tools_before = _trusted_tool_identities(core)
        before_artifacts, before_private = _artifact_snapshot(
            repository=repository,
            application_root=application_root,
            executable=executable,
            shim_artifact=shim_artifact,
            runtime_artifact=runtime_artifact,
            resolver_manifest=resolver_manifest,
            provider_dependencies=provider_dependencies,
            core=core,
        )
        payloads: list[bytes] = []
        expected_launches: list[tuple[str, int]] = []
        cpu_launches: list[dict[str, Any]] = []
        launch_observations: list[
            tuple[int, str, int, HostObservation, HostObservation]
        ] = []
        challenges: set[str] = set()

        for index in range(LAUNCH_COUNT):
            challenge = challenge_factory(index)
            if (
                not isinstance(challenge, str)
                or re.fullmatch(r"[0-9a-f]{64}", challenge) is None
                or challenge in challenges
            ):
                raise CpuBenchmarkCollectorError(
                    "every launch challenge must be unique lowercase 256-bit hex"
                )
            challenges.add(challenge)
            assert launch_parent_descriptor is not None
            private_owner = _create_owned_directory(
                launch_parent_descriptor,
                launch_parent_identity,
                f"{LAUNCH_PRIVATE_PREFIX}{secrets.token_hex(32)}",
                f"launch {index + 1} private directory",
            )
            private_root = launch_parent / private_owner.name
            private_cleanup_succeeded = False
            try:
                environment = launch_environment(
                    private_root,
                    challenge=challenge,
                    platform_id=platform_id,
                    display=display,
                )
                launch_cwd = private_root / "cwd"
                _verify_descriptor(
                    private_owner.descriptor,
                    private_owner.identity,
                    f"launch {index + 1} private directory",
                )
                if not _named_directory_matches(
                    launch_parent_descriptor,
                    private_owner,
                ):
                    raise CpuBenchmarkCollectorError(
                        f"launch {index + 1} private directory was replaced "
                        "before process start"
                    )
                _directory(launch_cwd, f"launch {index + 1} working directory")
                captured: list[int] = []

                def on_started(process_id: int) -> None:
                    if (
                        isinstance(process_id, bool)
                        or not isinstance(process_id, int)
                        or not 1 <= process_id <= MAX_PROCESS_ID
                        or captured
                    ):
                        raise CpuBenchmarkCollectorError(
                            "bounded runner returned an invalid direct-child PID"
                        )
                    captured.append(process_id)

                environment_before = environment_observer(
                    platform_id, architecture
                )
                before_usage = usage_reader()
                started = clock_reader()
                try:
                    output_value = runner(
                        (str(executable),),
                        operation=f"CPU benchmark launch {index + 1}",
                        cwd=launch_cwd,
                        environment=environment,
                        timeout_seconds=COLLECTION_TIMEOUT_SECONDS,
                        maximum_stdout_bytes=MAX_STDOUT_BYTES,
                        maximum_stderr_bytes=MAX_STDERR_BYTES,
                        on_started=on_started,
                    )
                except Exception as error:
                    raise CpuBenchmarkCollectorError(
                        f"CPU benchmark launch {index + 1} failed"
                    ) from error
                finished = clock_reader()
                after_usage = usage_reader()
                environment_after = environment_observer(
                    platform_id, architecture
                )
                if len(captured) != 1:
                    raise CpuBenchmarkCollectorError(
                        "bounded runner did not publish one direct-child PID"
                    )
                wall_microseconds = (finished - started) // 1000
                if wall_microseconds <= 0:
                    raise CpuBenchmarkCollectorError(
                        "launch wall duration is invalid"
                    )
                user_microseconds, system_microseconds = _usage_delta(
                    before_usage, after_usage, f"launch {index + 1}"
                )
                if (
                    core.canonical_application_tree_identity(
                        application_root,
                        label="application",
                        include_entries=True,
                    )
                    != before_private["tree"]
                ):
                    raise CpuBenchmarkCollectorError(
                        f"application identity changed after launch {index + 1}"
                    )
                process_id = captured[0]
                _fragment, payload, _digest = _parse_fragment_output(
                    output_value.stdout,
                    output_value.stderr,
                    platform_id=platform_id,
                    challenge=challenge,
                    process_id=process_id,
                    core=core,
                )
                payloads.append(payload)
                expected_launches.append((challenge, process_id))
                cpu_launches.append(
                    {
                        "index": index,
                        "launchChallenge": challenge,
                        "processId": process_id,
                        "userMicroseconds": user_microseconds,
                        "systemMicroseconds": system_microseconds,
                        "wallMicroseconds": wall_microseconds,
                    }
                )
                launch_observations.append(
                    (
                        index,
                        challenge,
                        process_id,
                        environment_before,
                        environment_after,
                    )
                )
            finally:
                private_cleanup_succeeded = _cleanup_owned_directory(
                    launch_parent_descriptor,
                    launch_parent_identity,
                    private_owner,
                )
                os.close(private_owner.descriptor)
            if not private_cleanup_succeeded:
                raise CpuBenchmarkCollectorError(
                    f"launch {index + 1} private directory was replaced or "
                    "could not be safely removed"
                )

        environment = _environment_record(
            platform_id=platform_id,
            architecture=architecture,
            launch_observations=launch_observations,
            cpu_launches=cpu_launches,
        )
        host_observation_payload = _host_observation_payload(environment)
        after_artifacts, after_private = _artifact_snapshot(
            repository=repository,
            application_root=application_root,
            executable=executable,
            shim_artifact=shim_artifact,
            runtime_artifact=runtime_artifact,
            resolver_manifest=resolver_manifest,
            provider_dependencies=provider_dependencies,
            core=core,
        )
        if before_artifacts != after_artifacts or before_private != after_private:
            raise CpuBenchmarkCollectorError(
                "application or artifact identity changed during collection"
            )
        tools_after = _trusted_tool_identities(core)
        if tools_before != tools_after:
            raise CpuBenchmarkCollectorError(
                "trusted collector or collection core identity changed during "
                "collection"
            )
        collection = core.derive_collection(
            fragment_payloads=payloads,
            host_observation_payload=host_observation_payload,
            expected_launches=expected_launches,
            artifacts=before_artifacts,
            collector_sha256=tools_before["collector"]["sha256"],
        )
        protected_roots = (
            (application_root, "application tree"),
            (repository, "repository"),
        )
        _verify_output_confinement(
            output=output,
            parent=parent,
            parent_descriptor=parent_descriptor,
            parent_identity=parent_identity,
            protected_roots=protected_roots,
        )
        output_owner = _create_owned_directory(
            parent_descriptor,
            parent_identity,
            output_name,
            "output directory",
        )
        _publish(
            parent_descriptor=parent_descriptor,
            parent_identity=parent_identity,
            output=output_owner,
            fragment_payloads=payloads,
            host_observation_payload=host_observation_payload,
            collection=collection,
        )
        _verify_output_confinement(
            output=output,
            parent=parent,
            parent_descriptor=parent_descriptor,
            parent_identity=parent_identity,
            protected_roots=protected_roots,
        )
        _verify_descriptor(parent_descriptor, parent_identity, "output parent")
        if not _named_directory_matches(parent_descriptor, output_owner):
            raise CpuBenchmarkCollectorError("output directory path was replaced")
        return collection
    finally:
        if output_owner is not None:
            os.close(output_owner.descriptor)
        if launch_parent_descriptor is not None:
            os.close(launch_parent_descriptor)
        os.close(parent_descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--application-root", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--shim-artifact", type=Path, required=True)
    parser.add_argument("--runtime-artifact", type=Path, required=True)
    parser.add_argument("--resolver-manifest", type=Path, required=True)
    parser.add_argument(
        "--provider-dependency", type=Path, action="append", default=[]
    )
    parser.add_argument("--display")
    parser.add_argument("--output-directory", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        record = collect(arguments)
    except (
        CpuBenchmarkCollectorError,
        cpu_benchmark_collection.CpuBenchmarkCollectionError,
        bounded_process.BoundedProcessError,
        FileNotFoundError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        print(f"collect_cpu_benchmark: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "result": "collected",
                "claimStatus": record["claimStatus"],
                "launchCount": record["launchCount"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
