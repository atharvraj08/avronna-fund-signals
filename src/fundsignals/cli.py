"""Command line entry point: `fundsignals run --week YYYY-MM-DD [--demo]`."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from fundsignals.config import load_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fundsignals")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="build the weekly ranked report")
    run.add_argument("--week", type=date.fromisoformat, help="any date in the report week")
    run.add_argument("--demo", action="store_true", help="use bundled fixtures, no keys needed")
    run.add_argument("--out", type=Path, help="output directory (default: output/)")
    run.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    from fundsignals.pipeline import run_pipeline

    env = load_env()
    demo = args.demo
    if not demo and not env:
        print("No keys configured in .env; falling back to demo mode.", file=sys.stderr)
        demo = True

    result = run_pipeline(week_of=args.week, demo=demo, out_dir=args.out, env=env)
    print(f"Week of {result.week_of}: {len(result.documents)} documents, "
          f"{len(result.signals)} signals, {len(result.scores)} funds ranked")
    for s in result.scores[:5]:
        print(f"  {s.rank:>2}. {s.fund_name:<30} {s.score:7.3f}  ({s.signal_count} signals)")
    for kind, path in result.outputs.items():
        print(f"  {kind:<5} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
