from __future__ import annotations

import math
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


_UNSET = object()
_SECRET_RE = re.compile(r"(^|_)(secret|token|password|passwd|pwd|credential|cookie|api_key|private_key|access_key|client_secret)($|_)", re.I)


class EnvCauseError(RuntimeError):
    """Raised for invalid inputs or an unusable reproduction command."""


@dataclass(frozen=True)
class EnvChange:
    key: str
    good: object
    bad: object

    @property
    def good_is_unset(self) -> bool:
        return self.good is _UNSET

    @property
    def bad_is_unset(self) -> bool:
        return self.bad is _UNSET


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    matched_failure: bool

    @property
    def combined_output(self) -> str:
        if self.stdout and self.stderr:
            return self.stdout + "\n" + self.stderr
        return self.stdout or self.stderr


@dataclass
class ReductionResult:
    changes: list[EnvChange]
    total_runs: int
    good_result: RunResult
    bad_result: RunResult


def parse_dotenv(path: str | os.PathLike[str]) -> dict[str, str]:
    """Parse a pragmatic subset of .env syntax without external dependencies.

    Supported:
      KEY=value
      export KEY=value
      quoted values with basic backslash escapes in double quotes
      inline comments for unquoted values when preceded by whitespace
      blank lines and comments

    Variable interpolation is intentionally not performed; the command receives
    literal values from the file.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise EnvCauseError(f"Environment file not found: {file_path}")

    result: dict[str, str] = {}
    for line_no, raw in enumerate(file_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise EnvCauseError(f"{file_path}:{line_no}: expected KEY=VALUE")

        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise EnvCauseError(f"{file_path}:{line_no}: invalid variable name {key!r}")

        value = value.strip()
        if value.startswith('"'):
            value = _parse_double_quoted(value, file_path, line_no)
        elif value.startswith("'"):
            value = _parse_single_quoted(value, file_path, line_no)
        else:
            # Treat `value # comment` as value, but preserve hashes without leading space.
            match = re.search(r"\s+#", value)
            if match:
                value = value[: match.start()].rstrip()
        result[key] = value
    return result


def _parse_double_quoted(value: str, path: Path, line_no: int) -> str:
    if len(value) < 2 or not value.endswith('"'):
        raise EnvCauseError(f"{path}:{line_no}: unterminated double-quoted value")
    inner = value[1:-1]
    try:
        return bytes(inner, "utf-8").decode("unicode_escape")
    except UnicodeDecodeError as exc:
        raise EnvCauseError(f"{path}:{line_no}: invalid escape sequence") from exc


def _parse_single_quoted(value: str, path: Path, line_no: int) -> str:
    if len(value) < 2 or not value.endswith("'"):
        raise EnvCauseError(f"{path}:{line_no}: unterminated single-quoted value")
    return value[1:-1]


def diff_envs(good: Mapping[str, str], bad: Mapping[str, str]) -> list[EnvChange]:
    keys = sorted(set(good) | set(bad))
    changes: list[EnvChange] = []
    for key in keys:
        good_value: object = good.get(key, _UNSET)
        bad_value: object = bad.get(key, _UNSET)
        if good_value != bad_value:
            changes.append(EnvChange(key=key, good=good_value, bad=bad_value))
    return changes


def build_environment(
    process_env: Mapping[str, str],
    good: Mapping[str, str],
    changes: Sequence[EnvChange],
    bad_keys: Iterable[str],
) -> dict[str, str]:
    """Build an execution environment from the good baseline plus chosen bad states."""
    env = dict(process_env)

    # Make the provided good config authoritative for every key it mentions.
    env.update(good)

    bad_key_set = set(bad_keys)
    for change in changes:
        if change.key in bad_key_set:
            if change.bad is _UNSET:
                env.pop(change.key, None)
            else:
                env[change.key] = str(change.bad)
        else:
            if change.good is _UNSET:
                env.pop(change.key, None)
            else:
                env[change.key] = str(change.good)
    return env


def run_command(
    command: Sequence[str],
    env: Mapping[str, str],
    *,
    contains: str | None = None,
    timeout: float | None = None,
    cwd: str | None = None,
) -> RunResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            env=dict(env),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if contains is None:
            matched_failure = completed.returncode != 0
        else:
            matched_failure = contains in (stdout + "\n" + stderr)
        return RunResult(
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_s=time.monotonic() - started,
            matched_failure=matched_failure,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        # A timeout is considered a failure when using exit-code mode. With
        # --contains, only the requested text should count as the target failure.
        matched_failure = contains is None or contains in (stdout + "\n" + stderr)
        return RunResult(
            returncode=124,
            stdout=stdout,
            stderr=stderr + ("\n" if stderr else "") + "envcause: command timed out",
            duration_s=time.monotonic() - started,
            matched_failure=matched_failure,
        )


def reproduce(
    command: Sequence[str],
    env: Mapping[str, str],
    *,
    contains: str | None,
    timeout: float | None,
    cwd: str | None,
    repeat: int,
) -> tuple[bool, RunResult, int]:
    """Return true only if the target failure reproduces on every repeat."""
    if repeat < 1:
        raise EnvCauseError("--repeat must be at least 1")
    last: RunResult | None = None
    for run_no in range(1, repeat + 1):
        last = run_command(command, env, contains=contains, timeout=timeout, cwd=cwd)
        if not last.matched_failure:
            return False, last, run_no
    assert last is not None
    return True, last, repeat


def ddmin(
    items: list[EnvChange],
    fails,
    *,
    max_tests: int | None = None,
) -> tuple[list[EnvChange], int]:
    """Classic delta debugging reduction.

    `fails(candidate)` must return True when candidate reproduces the target failure.
    The result is 1-minimal: removing any single remaining change no longer reproduces.
    """
    if not items:
        return [], 0

    candidate = list(items)
    n = 2
    tests = 0

    def check(subset: list[EnvChange]) -> bool:
        nonlocal tests
        if max_tests is not None and tests >= max_tests:
            raise EnvCauseError(f"Maximum reduction tests reached ({max_tests})")
        tests += 1
        return bool(fails(subset))

    while len(candidate) >= 2:
        chunk_size = math.ceil(len(candidate) / n)
        chunks = [candidate[i : i + chunk_size] for i in range(0, len(candidate), chunk_size)]
        reduced = False

        # First see whether one chunk alone is sufficient.
        for chunk in chunks:
            if check(chunk):
                candidate = chunk
                n = max(n - 1, 2)
                reduced = True
                break
        if reduced:
            continue

        # Then see whether removing a chunk preserves the failure.
        for chunk in chunks:
            chunk_ids = {id(x) for x in chunk}
            complement = [x for x in candidate if id(x) not in chunk_ids]
            if complement and check(complement):
                candidate = complement
                n = max(n - 1, 2)
                reduced = True
                break
        if reduced:
            continue

        if n >= len(candidate):
            break
        n = min(len(candidate), n * 2)

    # ddmin should be 1-minimal already, but this final pass makes the guarantee
    # explicit and easier to reason about for small configuration sets.
    i = 0
    while i < len(candidate):
        complement = candidate[:i] + candidate[i + 1 :]
        if complement and check(complement):
            candidate = complement
        else:
            i += 1

    return candidate, tests


def reduce_environment(
    good: Mapping[str, str],
    bad: Mapping[str, str],
    command: Sequence[str],
    *,
    contains: str | None = None,
    timeout: float | None = None,
    cwd: str | None = None,
    repeat: int = 1,
    max_tests: int | None = None,
    process_env: Mapping[str, str] | None = None,
) -> ReductionResult:
    if not command:
        raise EnvCauseError("No reproduction command supplied")

    process_env = process_env or os.environ
    changes = diff_envs(good, bad)
    if not changes:
        raise EnvCauseError("Good and bad environment files contain no differences")

    def env_for(subset: Sequence[EnvChange]) -> dict[str, str]:
        return build_environment(process_env, good, changes, (c.key for c in subset))

    good_failed, good_result, good_runs = reproduce(
        command,
        env_for([]),
        contains=contains,
        timeout=timeout,
        cwd=cwd,
        repeat=repeat,
    )
    if good_failed:
        target = f"output containing {contains!r}" if contains else "a non-zero exit code"
        raise EnvCauseError(f"Known-good configuration already reproduces {target}")

    bad_failed, bad_result, bad_runs = reproduce(
        command,
        env_for(changes),
        contains=contains,
        timeout=timeout,
        cwd=cwd,
        repeat=repeat,
    )
    if not bad_failed:
        target = f"output containing {contains!r}" if contains else "a non-zero exit code"
        raise EnvCauseError(f"Known-bad configuration does not reproduce {target}")

    command_runs = good_runs + bad_runs

    def fails(subset: Sequence[EnvChange]) -> bool:
        nonlocal command_runs
        failed, _, used = reproduce(
            command,
            env_for(subset),
            contains=contains,
            timeout=timeout,
            cwd=cwd,
            repeat=repeat,
        )
        command_runs += used
        return failed

    reduced, _logical_tests = ddmin(changes, fails, max_tests=max_tests)
    return ReductionResult(
        changes=reduced,
        total_runs=command_runs,
        good_result=good_result,
        bad_result=bad_result,
    )


def redact_value(key: str, value: object, *, show_values: bool = False) -> str:
    if value is _UNSET:
        return "<UNSET>"
    value_s = str(value)
    if show_values:
        return value_s
    if _SECRET_RE.search(key):
        return "<REDACTED>"
    if len(value_s) > 80:
        return value_s[:77] + "..."
    return value_s


def shell_assignment(change: EnvChange, *, show_values: bool = False) -> str:
    if change.bad is _UNSET:
        return f"unset {change.key}"
    value = redact_value(change.key, change.bad, show_values=show_values)
    if value == "<REDACTED>":
        return f"{change.key}=<REDACTED>"
    return f"{change.key}={shlex.quote(value)}"


def write_repro(path: str | os.PathLike[str], changes: Sequence[EnvChange]) -> None:
    lines = ["# Minimal failure-inducing configuration generated by envcause"]
    for change in changes:
        if change.bad is _UNSET:
            lines.append(f"# UNSET {change.key}")
        else:
            value = str(change.bad)
            # Quote values when doing so avoids ambiguity.
            if not value or re.search(r"\s|#|['\"]", value):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{change.key}="{escaped}"')
            else:
                lines.append(f"{change.key}={value}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
