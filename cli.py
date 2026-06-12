#!/usr/bin/env python3
"""
Self-Harness CLI
================
A thin orchestration layer over the existing optimization scripts.

Commands:
    status     Show current harness version, agreement rates, rounds run
    baseline   Run baseline evaluation (v0.1.0) on all records
    round      Run one optimization round from the current harness
    loop       Run optimization rounds until convergence
    eval       Evaluate the current harness on all records
    compare    Show the before/after benchmark comparison table
    history    Show every harness version with its metrics

This module only reads config/report files for display and shells out to the
scripts in ./scripts for anything that touches the model. It does not import or
modify anything under ./src.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
HARNESS_DIR = ROOT / "configs" / "harness"
VERSIONS_DIR = HARNESS_DIR / "versions"
DATA_DIR = ROOT / "data"
REPORTS_DIR = DATA_DIR / "reports"
RUNS_DIR = DATA_DIR / "runs"
PROPOSALS_DIR = DATA_DIR / "proposals"

BASELINE_VERSION = "v0.1.0"

# ---------------------------------------------------------------------------
# Optional rich support — degrade gracefully to plain stdlib output.
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box

    _console = Console()
    HAS_RICH = True
except Exception:  # pragma: no cover - cosmetic fallback only
    _console = None
    HAS_RICH = False


def out(msg: str = "") -> None:
    """Print a line, using rich markup when available."""
    if HAS_RICH:
        _console.print(msg)
    else:
        # Strip the most common rich markup tags for the plain fallback.
        import re

        print(re.sub(r"\[/?[^\]]*\]", "", msg))


def rule(title: str) -> None:
    if HAS_RICH:
        _console.rule(f"[bold cyan]{title}")
    else:
        print("\n" + "=" * 70)
        print(f"  {title}")
        print("=" * 70)


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


# ---------------------------------------------------------------------------
# Config / report readers (display only)
# ---------------------------------------------------------------------------
def _read_yaml(path: Path) -> dict:
    """Read a small flat YAML file. Uses PyYAML if present, else a tiny parser."""
    if not path.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        # Minimal fallback for the simple `key: value` files used here.
        data: dict = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, raw = line.partition(":")
            data[key.strip()] = raw.strip().strip('"')
        return data


def _read_json(path: Path) -> object | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def current_harness() -> dict:
    return _read_yaml(HARNESS_DIR / "current.yaml")


def discover_versions() -> list[str]:
    """All harness versions known from version prompt files, sorted."""
    versions = {BASELINE_VERSION}
    if VERSIONS_DIR.exists():
        versions |= {p.stem for p in VERSIONS_DIR.glob("v*.md")}
    if RUNS_DIR.exists():
        versions |= {d.name for d in RUNS_DIR.iterdir() if d.is_dir()}
    return sorted(versions)


def round_reports() -> list[tuple[str, dict]]:
    """All round_*.json reports as (version, payload) sorted by version."""
    if not REPORTS_DIR.exists():
        return []
    found = []
    for p in sorted(REPORTS_DIR.glob("round_*.json")):
        payload = _read_json(p)
        if isinstance(payload, dict):
            found.append((p.stem.replace("round_", ""), payload))
    return found


# ---------------------------------------------------------------------------
# Subprocess runner — streams script output live.
# ---------------------------------------------------------------------------
def run_script(name: str) -> int:
    """Run scripts/<name> from the project root, streaming output live."""
    script = SCRIPTS / name
    if not script.exists():
        out(f"[bold red]Script not found:[/] {script}")
        return 1

    rule(f"running {name}")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", str(script)],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as exc:  # pragma: no cover
        out(f"[bold red]Failed to launch script:[/] {exc}")
        return 1

    assert proc.stdout is not None
    for line in proc.stdout:
        # Stream raw script output verbatim (it already formats itself).
        sys.stdout.write(line)
        sys.stdout.flush()
    code = proc.wait()

    if code == 0:
        out("\n[bold green]✓ done[/]")
    else:
        out(f"\n[bold red]✗ exited with code {code}[/]")
    return code


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_status(_args: argparse.Namespace) -> int:
    cfg = current_harness()
    if not cfg:
        out("[yellow]No current.yaml found.[/] Run a baseline + round first.")
        return 1

    version = cfg.get("version", "?")
    based_on = cfg.get("based_on", "—")
    promoted = cfg.get("promoted_proposal", "—")
    delta_in = cfg.get("delta_in")
    delta_out = cfg.get("delta_out")
    rounds = round_reports()
    n_rounds = len(rounds)
    n_versions = len(discover_versions())

    # Try to pull current absolute agreement from the latest comparison report.
    comparison = _read_json(REPORTS_DIR / "comparison.json")
    agree_base = agree_opt = None
    if isinstance(comparison, dict):
        agree_base = comparison.get("base_stats", {}).get("overall_agreement")
        agree_opt = comparison.get("optimized_stats", {}).get("overall_agreement")

    rule("Self-Harness status")
    if HAS_RICH:
        table = Table(box=box.SIMPLE_HEAVY, show_header=False, pad_edge=False)
        table.add_column("k", style="dim")
        table.add_column("v", style="bold")
        table.add_row("Current version", f"[cyan]{version}[/]")
        table.add_row("Based on", str(based_on))
        table.add_row("Promoted proposal", str(promoted))
        table.add_row("Δ held_in (vs prev)", pct(delta_in))
        table.add_row("Δ held_out (vs prev)", pct(delta_out))
        table.add_row("Baseline agreement", pct(agree_base))
        table.add_row("Current agreement", pct(agree_opt))
        table.add_row("Rounds run", str(n_rounds))
        table.add_row("Versions on disk", str(n_versions))
        _console.print(table)
    else:
        print(f"  Current version      : {version}")
        print(f"  Based on             : {based_on}")
        print(f"  Promoted proposal    : {promoted}")
        print(f"  Δ held_in (vs prev)  : {pct(delta_in)}")
        print(f"  Δ held_out (vs prev) : {pct(delta_out)}")
        print(f"  Baseline agreement   : {pct(agree_base)}")
        print(f"  Current agreement    : {pct(agree_opt)}")
        print(f"  Rounds run           : {n_rounds}")
        print(f"  Versions on disk     : {n_versions}")

    if agree_opt is None:
        out("\n[dim]Tip: run `compare` to compute current absolute agreement.[/]")
    return 0


def cmd_history(_args: argparse.Namespace) -> int:
    rounds = round_reports()
    rule("version history")

    if not rounds:
        out("[yellow]No optimization rounds recorded yet.[/]")
        out("[dim]Run `baseline` then `round` to create history.[/]")
        return 0

    rows = []
    # Baseline anchor row from the first round's baseline numbers.
    first = rounds[0][1]
    rows.append(
        (
            BASELINE_VERSION,
            "—",
            "—",
            pct(first.get("baseline_in")),
            pct(first.get("baseline_out")),
            "baseline",
        )
    )
    for from_ver, r in rounds:
        if r.get("promoted"):
            d_in = (r.get("final_in", 0) - r.get("baseline_in", 0))
            d_out = (r.get("final_out", 0) - r.get("baseline_out", 0))
            rows.append(
                (
                    r.get("new_version", "?"),
                    f"{d_in:+.1%}",
                    f"{d_out:+.1%}",
                    pct(r.get("final_in")),
                    pct(r.get("final_out")),
                    f"from {from_ver}",
                )
            )
        else:
            rows.append(
                ("(no change)", "—", "—",
                 pct(r.get("baseline_in")), pct(r.get("baseline_out")),
                 f"from {from_ver}")
            )

    if HAS_RICH:
        table = Table(box=box.ROUNDED, header_style="bold cyan")
        table.add_column("Version")
        table.add_column("Δ in", justify="right")
        table.add_column("Δ out", justify="right")
        table.add_column("held_in", justify="right")
        table.add_column("held_out", justify="right")
        table.add_column("Note", style="dim")
        for ver, di, do, hin, hout, note in rows:
            style = "green" if di.startswith("+") else None
            table.add_row(ver, f"[{style}]{di}[/]" if style else di, do, hin, hout, note)
        _console.print(table)
    else:
        hdr = f"{'Version':<14}{'Δ in':>8}{'Δ out':>8}{'held_in':>9}{'held_out':>10}  Note"
        print(hdr)
        print("-" * len(hdr))
        for ver, di, do, hin, hout, note in rows:
            print(f"{ver:<14}{di:>8}{do:>8}{hin:>9}{hout:>10}  {note}")
    return 0


def cmd_baseline(_args: argparse.Namespace) -> int:
    return run_script("run_baseline.py")


def cmd_round(_args: argparse.Namespace) -> int:
    return run_script("run_round.py")


def cmd_loop(_args: argparse.Namespace) -> int:
    return run_script("run_loop.py")


def cmd_eval(_args: argparse.Namespace) -> int:
    return run_script("run_optimized.py")


def cmd_compare(_args: argparse.Namespace) -> int:
    return run_script("benchmark_compare.py")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
COMMANDS = {
    "status": (cmd_status, "Show current harness version, agreement, rounds run"),
    "baseline": (cmd_baseline, "Run baseline evaluation (v0.1.0) on all records"),
    "round": (cmd_round, "Run one optimization round from the current harness"),
    "loop": (cmd_loop, "Run optimization rounds until convergence"),
    "eval": (cmd_eval, "Evaluate the current harness on all records"),
    "compare": (cmd_compare, "Show the before/after benchmark comparison table"),
    "history": (cmd_history, "Show every harness version with its metrics"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Self-Harness optimization pipeline control surface.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
        "  python3 cli.py status\n"
        "  python3 cli.py baseline\n"
        "  python3 cli.py loop\n"
        "  python3 cli.py compare\n",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name, (_fn, help_text) in COMMANDS.items():
        sub.add_parser(name, help=help_text)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    fn, _ = COMMANDS[args.command]
    try:
        return fn(args)
    except KeyboardInterrupt:
        out("\n[yellow]Interrupted.[/]")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
