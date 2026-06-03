#!/usr/bin/env python3
"""Create or refresh paper_trading_india.xlsx for Excel-based paper trading."""

from __future__ import annotations

import argparse
from pathlib import Path

from excel_workbook import (
    create_workbook,
    default_output_path,
    load_json_backup,
    refresh_workbook,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=f"Output path (default: {default_output_path()})",
    )
    parser.add_argument(
        "--import",
        dest="import_path",
        type=Path,
        metavar="JSON",
        help="Import trades from a paper_trading_backup.json export",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Recompute Completed Trades from Trade Log (run after adding rows)",
    )
    args = parser.parse_args()
    output = args.output or default_output_path()

    if args.refresh:
        if not output.is_file():
            raise SystemExit(f"File not found: {output}. Create it first without --refresh.")
        refresh_workbook(output)
        print(f"Refreshed completed trades: {output}")
        return

    import_json = None
    if args.import_path:
        if not args.import_path.is_file():
            raise SystemExit(f"Import file not found: {args.import_path}")
        import_json = load_json_backup(args.import_path)
        n = len(import_json.get("trades", []))
        print(f"Importing {n} trade(s) from {args.import_path}")

    create_workbook(output, import_json=import_json)
    print(f"Created: {output}")
    if import_json:
        print("Open in Excel, enter LTP on Open Positions, then save.")
    print("After new trades: python build_paper_trading_sheet.py --refresh")


if __name__ == "__main__":
    main()
