from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import EnvCauseError, diff_envs, parse_dotenv, redact_value, reduce_environment, shell_assignment, write_repro


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="envcause",
        description="Find the smallest configuration change set that reproduces a failure.",
        epilog="Example: envcause --good .env.local --bad .env.staging -- pytest -q",
    )
    parser.add_argument("--good", required=True, help="Known-good .env file")
    parser.add_argument("--bad", required=True, help="Known-bad .env file")
    parser.add_argument(
        "--contains",
        help="Treat the run as failing only when stdout/stderr contains this text",
    )
    parser.add_argument("--repeat", type=int, default=1, help="Require failure to reproduce N times (default: 1)")
    parser.add_argument("--timeout", type=float, help="Per-run timeout in seconds")
    parser.add_argument("--cwd", help="Working directory for the reproduction command")
    parser.add_argument("--max-tests", type=int, help="Maximum candidate subsets tested during reduction")
    parser.add_argument("--write-repro", metavar="PATH", help="Write the reduced bad configuration to a .env file")
    parser.add_argument(
        "--show-values",
        action="store_true",
        help="Show values in the report (secret-looking keys are redacted by default)",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run; place it after --, e.g. -- pytest tests/test_login.py",
    )
    return parser


def _strip_separator(command: list[str]) -> list[str]:
    return command[1:] if command and command[0] == "--" else command


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = _strip_separator(args.command)
    if not command:
        print("envcause: error: provide a reproduction command after --", file=sys.stderr)
        return 2

    try:
        good = parse_dotenv(args.good)
        bad = parse_dotenv(args.bad)
        all_changes = diff_envs(good, bad)

        print("EnvCause")
        print("=" * 72)
        print(f"Good config : {Path(args.good)}")
        print(f"Bad config  : {Path(args.bad)}")
        print(f"Differences : {len(all_changes)}")
        print(f"Command     : {' '.join(command)}")
        if args.contains:
            print(f"Failure     : output contains {args.contains!r}")
        else:
            print("Failure     : non-zero exit code")
        if args.repeat > 1:
            print(f"Repeat      : {args.repeat} consecutive reproductions required")
        print()
        print("Verifying known-good and known-bad configs, then reducing...")

        result = reduce_environment(
            good,
            bad,
            command,
            contains=args.contains,
            timeout=args.timeout,
            cwd=args.cwd,
            repeat=args.repeat,
            max_tests=args.max_tests,
        )

        print()
        print("Result")
        print("-" * 72)
        print(f"Original differing variables : {len(all_changes)}")
        print(f"Failure-inducing variables    : {len(result.changes)}")
        print(f"Command executions            : {result.total_runs}")
        print()
        print("1-minimal failure-inducing change set:")
        for change in result.changes:
            good_value = redact_value(change.key, change.good, show_values=args.show_values)
            bad_value = redact_value(change.key, change.bad, show_values=args.show_values)
            print(f"  {change.key}: {good_value} -> {bad_value}")

        print()
        print("Bad-state reproduction:")
        for change in result.changes:
            print(f"  {shell_assignment(change, show_values=args.show_values)}")

        if args.write_repro:
            write_repro(args.write_repro, result.changes)
            print()
            print(f"Wrote reproduction config: {args.write_repro}")
            if not args.show_values:
                print("Note: the file contains real values even though terminal output may be redacted.")
        return 0

    except EnvCauseError as exc:
        print(f"envcause: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"envcause: command not found: {exc.filename}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
