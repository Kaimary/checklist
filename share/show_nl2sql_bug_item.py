#!/usr/bin/env python3
"""
Utility script to inspect aligned entries from the NL2SQL bugs dataset
and its corresponding Guardian judgment output.

Given a 1-based index n, it prints:
  * question/sql from NL2SQL-Bugs_20_percent.json
  * results/traces from the matching line in the guardian+gpt-5.1(qry) jsonl log
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, List


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_PATH = REPO_ROOT / "data" / "nl2sql-bugs" / "NL2SQL-Bugs_20_percent.json"
DEFAULT_JUDGMENTS_PATH = (
    REPO_ROOT
    / "data"
    / "nl2sql-bugs"
    / "results"
    / "judgments,dataset=nl2sql-bugs-20%,judge=guardian+gpt-5.1(qry).jsonl"
)
DEFAULT_TESTER_NAME = "query_review"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Show the nth NL2SQL-Bugs entry alongside the nth Guardian judgment "
            "result (1-indexed)."
        )
    )
    parser.add_argument("index", type=int, help="1-based index of the entry to display")
    parser.add_argument(
        "--data-file",
        type=Path,
        default=DEFAULT_DATA_PATH,
        help=f"Path to NL2SQL-Bugs_20_percent.json (default: {DEFAULT_DATA_PATH})",
    )
    parser.add_argument(
        "--tester-name",
        type=str,
        default=DEFAULT_TESTER_NAME,
        help=f"Name of the tester (default: {DEFAULT_TESTER_NAME})",
    )
    parser.add_argument(
        "--judgments-file",
        type=Path,
        default=DEFAULT_JUDGMENTS_PATH,
        help=(
            "Path to judgments,dataset=nl2sql-bugs-20%,judge=guardian+gpt-5.1(qry).jsonl "
            f"(default: {DEFAULT_JUDGMENTS_PATH})"
        ),
    )
    return parser.parse_args()


def load_json_entries(path: Path) -> List[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl_line(path: Path, index: int) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        for current, line in enumerate(handle, start=1):
            if current == index:
                return json.loads(line)
    raise IndexError(f"Index {index} exceeds number of lines in {path}")


def validate_index(index: int, length: int, label: str) -> None:
    if index < 1 or index > length:
        raise IndexError(f"{label} index {index} out of range: 1 <= n <= {length}")


def format_list(values: Iterable[Any]) -> str:
    return json.dumps(list(values), ensure_ascii=False)


def main() -> None:
    args = parse_args()

    entries = load_json_entries(args.data_file)
    validate_index(args.index, len(entries), "Dataset")
    entry = entries[args.index - 1]

    judgment = read_jsonl_line(args.judgments_file, args.index)
    query_review = judgment.get(args.tester_name, {})
    results = query_review.get("results")
    traces = query_review.get("traces")

    print(f"Dataset Entry #{args.index}")
    print(f"DB_ID: {entry.get('db_id', '<missing>')}")
    print(f"Question: {entry.get('question', '<missing>')}")
    print(f"SQL: {entry.get('sql', '<missing>')}")
    print(f"Gold: {entry.get('gold_sql', '<missing>')}")
    print()
    print(f"Judgment Line #{args.index}")
    print(f"Results: {format_list(results or [])}")
    print("Traces:")
    if isinstance(traces, list):
        for idx, trace in enumerate(traces, start=1):
            print(f"--- Trace {idx} ---")
            print(trace.rstrip())
    else:
        print("<missing traces>")


if __name__ == "__main__":
    main()
