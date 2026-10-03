"""Run with python -m src.operator.report RUN_ID --source RUN_DIRECTORY."""

import argparse
import sqlite3
from pathlib import Path

from .viewer import write_report


def main() -> None:
    """Select a saved run and write one portable HTML file."""
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("run_id", nargs="?")
    choice.add_argument("--latest", action="store_true")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("."),
        help="Saved run directory or parent of run directories",
    )
    parser.add_argument("--output", type=Path, default=Path("report.html"))
    args = parser.parse_args()
    try:
        path = write_report(args.source, args.run_id, args.output)
    except (ValueError, OSError, sqlite3.DatabaseError) as error:
        # Input content and database error details can contain capabilities.
        parser.exit(
            2,
            f"Report unavailable ({type(error).__name__}); check source, run ID and local evidence.\n",
        )
    print(f"Report written: {path}")


if __name__ == "__main__":
    main()
