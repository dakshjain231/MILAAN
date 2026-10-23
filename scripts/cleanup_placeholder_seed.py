#!/usr/bin/env python3
"""Remove only confirmed synthetic agent records that use a placeholder phone.

A previous demo seed was run with the literal trader value ``+91``.  Those
seven bills are synthetic (``seed:photo`` provenance), but they sit in the
same agent database as real conversations and would inflate the dashboard.
This script:

* opens the agent database read-only first and lists candidates;
* refuses to touch a trader that owns any non-seed invoice;
* writes a timestamped SQLite backup before changing anything;
* deletes only rows belonging to the placeholder trader and its suppliers;
* prints counts, never full phone numbers.

Usage:
    .venv/bin/python scripts/cleanup_placeholder_seed.py --list
    .venv/bin/python scripts/cleanup_placeholder_seed.py --confirm
    .venv/bin/python scripts/cleanup_placeholder_seed.py --db /tmp/x.db --confirm
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "agent_data" / "milaan_agent.db"


def mask_phone(value: str | None) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        return "(empty)"
    if len(digits) <= 4:
        return "…"
    return "+" + digits[:2] + "•" * max(0, len(digits) - 6) + digits[-4:]


def placeholder(phone: str | None) -> bool:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return len(digits) < 10


def inventory(connection: sqlite3.Connection, phone: str) -> dict:
    connection.row_factory = sqlite3.Row
    invoices = connection.execute(
        "SELECT id, provenance FROM agent_invoices WHERE trader_phone=?",
        (phone,),
    ).fetchall()
    suppliers = connection.execute(
        "SELECT id, gstin, phone FROM agent_suppliers WHERE trader_phone=?",
        (phone,),
    ).fetchall()
    return {
        "invoices": [dict(row) for row in invoices],
        "suppliers": [dict(row) for row in suppliers],
    }


def backup(database: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"milaan_agent_pre_cleanup_{stamp}.db"
    with sqlite3.connect(database) as source, sqlite3.connect(target) as dest:
        source.backup(dest)
    return target


def delete_trader(connection: sqlite3.Connection, phone: str, suppliers: list[dict]) -> dict:
    supplier_phones = {
        str(item.get("phone")) for item in suppliers if item.get("phone")
    }
    shared = set()
    for supplier_phone in supplier_phones:
        count = connection.execute(
            "SELECT COUNT(DISTINCT trader_phone) FROM agent_suppliers "
            "WHERE phone=? AND trader_phone<>?",
            (supplier_phone, phone),
        ).fetchone()[0]
        if count:
            shared.add(supplier_phone)
    deletable_phones = {phone} | (supplier_phones - shared)

    import_ids = [
        row[0]
        for row in connection.execute(
            "SELECT id FROM agent_2b_imports WHERE trader_phone=?", (phone,)
        )
    ]
    counts: dict[str, int] = {}

    def run(label: str, sql: str, args: tuple) -> None:
        cursor = connection.execute(sql, args)
        counts[label] = cursor.rowcount

    if import_ids:
        placeholders = ",".join("?" for _ in import_ids)
        run(
            "agent_2b_rows",
            f"DELETE FROM agent_2b_rows WHERE import_id IN ({placeholders})",
            tuple(import_ids),
        )
    run("agent_2b_imports", "DELETE FROM agent_2b_imports WHERE trader_phone=?", (phone,))
    run("agent_2b_history", "DELETE FROM agent_2b_history WHERE trader_phone=?", (phone,))
    run("agent_pending", "DELETE FROM agent_pending WHERE trader_phone=?", (phone,))
    run("agent_outbound", "DELETE FROM agent_outbound WHERE trader_phone=?", (phone,))
    run("agent_trace", "DELETE FROM agent_trace WHERE trader_phone=?", (phone,))
    run("agent_invoices", "DELETE FROM agent_invoices WHERE trader_phone=?", (phone,))

    if deletable_phones:
        placeholders = ",".join("?" for _ in deletable_phones)
        run(
            "agent_messages",
            f"DELETE FROM agent_messages WHERE phone IN ({placeholders})",
            tuple(sorted(deletable_phones)),
        )
    run("agent_suppliers", "DELETE FROM agent_suppliers WHERE trader_phone=?", (phone,))
    run("agent_traders", "DELETE FROM agent_traders WHERE phone=?", (phone,))
    return counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "backups")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    database = args.db
    if not database.is_file():
        print(f"Agent database not found: {database}")
        return 1

    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        traders = [dict(row) for row in connection.execute(
            "SELECT phone, name, created_at FROM agent_traders ORDER BY created_at"
        )]
        candidates = [row for row in traders if placeholder(row.get("phone"))]
        if not candidates:
            print("No placeholder trader phones found. Nothing to clean.")
            return 0

        print("Placeholder trader candidates:")
        plans = []
        for row in candidates:
            info = inventory(connection, row["phone"])
            provenance = {item.get("provenance") or "" for item in info["invoices"]}
            synthetic = all(item.startswith("seed:") for item in provenance) if provenance else True
            print(
                f"  {mask_phone(row['phone'])} · {row.get('name') or 'Trader'} · "
                f"{len(info['invoices'])} invoice(s) · provenance={sorted(provenance) or ['(none)']} · "
                f"{'confirmed synthetic' if synthetic else 'NOT synthetic — will skip'}"
            )
            if synthetic:
                plans.append((row, info))

        if not plans:
            print("Nothing was changed: every placeholder trader owns non-seed data.")
            return 1

        if not args.confirm:
            print("\nDry run. Re-run with --confirm to back up and remove these records.")
            return 0

    backup_path = backup(database, args.backup_dir)
    print(f"Backup written: {backup_path}")

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        total = {}
        for row, info in plans:
            counts = delete_trader(connection, row["phone"], info["suppliers"])
            for key, value in counts.items():
                total[key] = total.get(key, 0) + value
            print(f"Removed placeholder trader {mask_phone(row['phone'])}")
        connection.commit()
    for key in sorted(total):
        print(f"  {key}: {total[key]}")
    print("Done. Only confirmed placeholder/seed rows were removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
