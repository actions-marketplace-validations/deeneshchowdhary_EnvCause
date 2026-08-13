from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path

from .core import EnvCauseError


def build_explain_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="envcause explain",
        description="Render an EnvCause JSON report as a shareable diagnosis.",
    )
    parser.add_argument("report", help="JSON report created with --report-json")
    parser.add_argument(
        "--format", choices=("terminal", "markdown"), default="terminal",
        help="Output format (default: terminal)",
    )
    parser.add_argument("--output", metavar="PATH", help="Write output to a file instead of stdout")
    return parser


def load_report(path: str) -> dict[str, object]:
    report_path = Path(path)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EnvCauseError(f"Report not found: {report_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvCauseError(f"Could not read report {report_path}: {exc}") from exc
    required = {
        "schema_version", "good_config", "bad_config", "command", "execution",
        "failure_match", "original_difference_count", "failure_inducing_count",
        "command_executions", "changes",
    }
    if report.get("schema_version") != 1 or not required.issubset(report):
        raise EnvCauseError(f"Unsupported or incomplete EnvCause report: {report_path}")
    if not isinstance(report["command"], list) or not isinstance(report["changes"], list):
        raise EnvCauseError(f"Invalid EnvCause report: {report_path}")
    return report


def _matcher(value: object) -> str:
    if not isinstance(value, dict):
        return "unknown failure condition"
    if value.get("nonzero_exit"):
        return "non-zero exit code"
    if value.get("contains") is not None:
        return f"output contains {value['contains']!r}"
    if value.get("matches") is not None:
        return f"output matches {value['matches']!r}"
    if value.get("junit") is not None:
        return f"failing JUnit report at {value['junit']}"
    return "unknown failure condition"


def _execution(value: object) -> str:
    if not isinstance(value, dict):
        return "unknown"
    kind = str(value.get("type", "unknown"))
    if kind == "docker":
        return f"Docker ({value.get('image', 'unknown image')})"
    if kind == "kubernetes":
        return f"Kubernetes ({value.get('pod', 'unknown pod')})"
    return kind


def _changes(report: dict[str, object]) -> list[dict[str, object]]:
    return [item for item in report["changes"] if isinstance(item, dict)]  # type: ignore[union-attr]


def render_terminal(report: dict[str, object]) -> str:
    command = shlex.join(str(part) for part in report["command"])  # type: ignore[union-attr]
    lines = [
        "EnvCause diagnosis",
        "=" * 72,
        f"Result      : {report['failure_inducing_count']} failure-inducing changes "
        f"from {report['original_difference_count']} differences",
        f"Good config : {report['good_config']}",
        f"Bad config  : {report['bad_config']}",
        f"Format      : {report.get('config_format', 'dotenv')}",
        f"Execution   : {_execution(report['execution'])}",
        f"Failure     : {_matcher(report['failure_match'])}",
        f"Command     : {command}",
        f"Runs        : {report['command_executions']} ({report.get('cache_hits', 0)} cache hits)",
        "",
        "1-minimal failure-inducing change set:",
    ]
    for change in _changes(report):
        lines.append(f"  {change.get('key')}: {change.get('good')} -> {change.get('bad')}")
    artifact = report.get("reproduction_config") or report.get("config_output")
    if artifact:
        lines.extend(("", f"Minimal reproduction: {artifact}"))
    lines.extend(("", "Reproduce:", f"  {command}"))
    return "\n".join(lines) + "\n"


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, object]) -> str:
    command = shlex.join(str(part) for part in report["command"])  # type: ignore[union-attr]
    lines = [
        "# EnvCause diagnosis", "",
        f"EnvCause reduced **{report['original_difference_count']}** differences to "
        f"**{report['failure_inducing_count']}** failure-inducing changes.", "",
        "## Context", "",
        f"- Good config: `{report['good_config']}`",
        f"- Bad config: `{report['bad_config']}`",
        f"- Format: `{report.get('config_format', 'dotenv')}`",
        f"- Execution: {_execution(report['execution'])}",
        f"- Failure condition: {_matcher(report['failure_match'])}",
        f"- Command executions: {report['command_executions']} ({report.get('cache_hits', 0)} cache hits)",
        "", "## Failure-inducing changes", "",
        "| Path or variable | Good state | Bad state |",
        "| --- | --- | --- |",
    ]
    for change in _changes(report):
        lines.append(
            f"| `{_cell(change.get('key'))}` | `{_cell(change.get('good'))}` | "
            f"`{_cell(change.get('bad'))}` |"
        )
    artifact = report.get("reproduction_config") or report.get("config_output")
    if artifact:
        lines.extend(("", f"Minimal reproduction config: `{artifact}`"))
    lines.extend(("", "## Reproduce", "", "```sh", command, "```", ""))
    return "\n".join(lines)


def explain_main(argv: list[str]) -> int:
    args = build_explain_parser().parse_args(argv)
    report = load_report(args.report)
    rendered = render_markdown(report) if args.format == "markdown" else render_terminal(report)
    if args.output:
        output = Path(args.output)
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
        except OSError as exc:
            raise EnvCauseError(f"Could not write explanation {output}: {exc}") from exc
    else:
        print(rendered, end="")
    return 0
