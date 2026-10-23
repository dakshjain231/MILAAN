"""Reset the agent database and load the demo month.

  python -m agent.demo.seed --trader +9198XXXXXXXX --supplier +9199XXXXXXXX [--supplier2 +91...]

The trader phone is the one that will chat with MILAAN. The supplier
phone(s) are CONSENTING test recipients (e.g. your teammate) standing in for
Mahadev Hardware / Rathore Electricals — disclosed in the pitch.
Seeded bills go through the same pipeline (fixture reader instead of the
vision model, because they are pre-loaded); the live bills L1/L2 are sent
from the phone during the demo.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .. import store
from ..channel import ConsoleChannel
from ..config import ROOT, get_settings, normalize_phone
from . import scenario
from .fixtures import FixtureExtractor

DEMO_DIR = ROOT / "agent_data" / "demo"


def _require_plausible_phone(value: str, label: str) -> str:
    """Reject placeholders such as ``+91`` before a seed can write anything."""
    phone = normalize_phone(value)
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 10 or len(digits) > 15:
        raise SystemExit(
            f"{label} must contain 10–15 digits (got {len(digits)}). "
            "Placeholder values such as +91 are refused."
        )
    if len(set(digits)) <= 2:
        raise SystemExit(f"{label} looks like a repeated-digit placeholder.")
    if "1234567890" in digits or "0123456789" in digits:
        raise SystemExit(f"{label} looks like a literal example phone number.")
    return phone


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trader", required=True)
    ap.add_argument("--supplier", required=True, help="consenting test phone for Mahadev Hardware")
    ap.add_argument("--supplier2", default=None, help="consenting test phone for Rathore Electricals")
    ap.add_argument("--keep", action="store_true", help="do not delete the existing agent DB")
    ap.add_argument("--force-default-db", action="store_true",
                    help="allow resetting agent_data/milaan_agent.db; back it up first")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    trader = _require_plausible_phone(a.trader, "--trader")
    supplier = _require_plausible_phone(a.supplier, "--supplier")
    supplier2 = _require_plausible_phone(a.supplier2, "--supplier2") if a.supplier2 else None
    if not a.keep and not os.environ.get("AGENT_DB_PATH") and not a.force_default_db:
        raise SystemExit(
            "Refusing to reset the default agent database. Set "
            "AGENT_DB_PATH=/tmp/milaan-agent-demo.db for a disposable demo, "
            "use --keep, or pass --force-default-db after taking a backup."
        )

    s = get_settings()
    if not a.keep:
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(s.db_path) + suffix)
            if p.exists():
                p.unlink()
        store.reset_initialized()
    info = scenario.build(DEMO_DIR)
    store.ensure_trader(trader, "hi")
    with store.conn() as c:
        c.execute("UPDATE agent_traders SET name=? WHERE phone=?", ("रमेश हार्डवेयर / Ramesh Hardware", trader))
    for gstin, periods in info["history"].items():
        for p in periods:
            store.add_history(trader, gstin, p)
    phones = {"mahadev": supplier, "rathore": supplier2 or supplier}
    for key, (name, gstin, _addr) in scenario.S.items():
        store.upsert_supplier(trader, gstin, name, phone=phones.get(key), consent=1 if key in phones else 0)

    from ..core import Agent
    os.environ.setdefault("DEMO_COMMANDS", "1")
    agent = Agent(channel=ConsoleChannel(echo=False), settings=s, fixture_extractor=FixtureExtractor(DEMO_DIR))
    for key in scenario.SEEDED:
        jpg = Path(info["bills"][key])
        agent.handle_bill(trader, jpg.read_bytes(), "image/jpeg", "seed")
    with store.conn() as c:
        c.execute("UPDATE agent_invoices SET provenance='seed:'||provenance")
        # pre-loaded bills were never sent over WhatsApp: drop their console replies
        c.execute("DELETE FROM agent_messages")
        c.execute("DELETE FROM agent_trace WHERE turn_id='seed' AND kind='MESSAGE'")
    invs = store.get_invoices(trader)
    if a.quiet:
        return
    print(f"Seeded {len(invs)} bills for {trader}:")
    for i in invs:
        print(f"  {i['id']:>2} {i['supplier_name'][:28]:<28} {i['invoice_number']:<16} {i['state']:<11} "
              f"GST ₹{(i['total_gst_paise'] or 0)/100:,.2f}")
    print(f"\nLive demo files in {DEMO_DIR}:")
    print("  bills/L1_marudhar_1214.jpg   (clean bill -> recorded)")
    print("  bills/L2_rathore_3341.jpg    (smudged GSTIN -> targeted re-ask)")
    print("  bills/X_not_a_bill.jpg       (rejected)")
    print("  GSTR2B_OCT2026.json          (send as a document -> reconciliation)")
    print("  GSTR2B_NOV2026.json          (next month -> RECOVERED)")
    print(f"\nRathore GSTIN to type when asked: {scenario.S['rathore'][1]}")


if __name__ == "__main__":
    main()
