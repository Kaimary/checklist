#!/usr/bin/env python3
"""Count SQL statements whose result set is empty."""

from __future__ import annotations

import argparse
import copy
import json
import sqlite3
from pathlib import Path
from typing import Dict

from tqdm import tqdm

from checklist.red.parser.red_parser import Query
from checklist.utils import get_red_schemas


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Execute each 'sql' entry in the NL2SQL bugs dataset and "
            "count how many return an empty result."
        )
    )
    parser.add_argument(
        "--dataset",
        default="data/nl2sql-bugs/NL2SQL-Bugs_20_percent.json",
        help="Path to the NL2SQL bugs JSON file.",
    )
    parser.add_argument(
        "--db-root",
        default="data/bird/databases",
        help="Root folder containing the per-database SQLite files.",
    )
    parser.add_argument(
        "--schema-path",
        default="data/nl2sql-bugs/tables.json",
        help="File containing the database schemata.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-query execution info.",
    )
    return parser.parse_args()


def _build_sub_sqls(query: Query):
    order = [c for c in Query._check_order if c in query.clauses and c not in ["SELECT", "FROM"]]
    # always make `SELECT ... FROM ...` subsql as the first one to ensure each subsqls executable
    active = {"SELECT", "FROM"}
    sql = " ".join(query.clauses[name].sql_str for name in Query._check_order if name in active)
    sub_sqls = [sql]

    for clause in order:
        active.add(clause)
        sql = " ".join(
            query.clauses[name].sql_str
            for name in Query._check_order
            if name in active
        )
        sub_sqls.append(sql.strip())
    return sub_sqls

def main() -> None:
    args = parse_args()
    dataset_path = Path(args.dataset)
    db_root = Path(args.db_root)
    schema_path = Path(args.schema_path)

    with dataset_path.open("r", encoding="utf-8") as f:
        entries = json.load(f)

    connections: Dict[str, sqlite3.Connection] = {}
    total = 0
    empty_results = 0
    immediate_empty_results = 0
    errors = 0

    red_schemas = get_red_schemas(entries, db_root, schema_path)
    try:
        for iidx, entry in tqdm(enumerate(entries)):
            if iidx == 63: continue
            total += 1
            db_id = entry["db_id"]
            sql = entry["sql"]
            label = entry["label"]
            db_path = db_root / db_id / f"{db_id}.sqlite"
            if not db_path.exists():
                if args.verbose:
                    print(f"[MISSING DB] id={entry['id']} db={db_id} path={db_path}")
                continue

            if db_id not in connections:
                connections[db_id] = sqlite3.connect(db_path)

            # try:
            #     parsed_query = Query(sql, copy.deepcopy(red_schemas[db_id]))
            #     subsqls = _build_sub_sqls(parsed_query)
            # except Exception as e:
            #     continue
            subsqls = [sql]
            conn = connections[db_id]
            check = False
            for idx, subsql in enumerate(subsqls):
                try:
                    cursor = conn.execute(subsql)
                    rows = cursor.fetchall()
                except sqlite3.Error as exc:
                    if args.verbose:
                        print(f"[ERROR] id={entry['id']} db={db_id}: {exc}")
                    continue

                if not rows:
                    if idx + 1 == len(subsqls): 
                        print(f"index: {iidx+1}")
                        empty_results += 1
                    elif not check: 
                        immediate_empty_results += 1
                        check = True
                    if not label: errors += 1
    finally:
        for conn in connections.values():
            conn.close()

    print(f"Total queries: {total}")
    print(f"Empty result sets: {empty_results}")
    print(f"Immediate Empty result sets: {immediate_empty_results}")
    print(f"Errors: {errors}")


if __name__ == "__main__":
    main()
