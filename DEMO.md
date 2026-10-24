# MILAAN SARCathon demo

Two parts, both fully synthetic:

* **A. The trader workspace** (six-page web UI): import bills and a
  GST-portal GSTR-2B, reconcile, prepare and approve a supplier follow-up,
  then verify recovery against the next month's GSTR-2B.
* **B. The WhatsApp agent loop** (offline simulator): bill photos, targeted
  re-ask, diagnosis, consent gate, and next-cycle recovery.

Nothing in this script touches `milaan.db`, and no WhatsApp message is sent
unless you deliberately use the guarded test-send in Settings.

## Setup (before the presentation)

```bash
./stop_milaan_demo.sh                 # if the normal workspace is running
rm -f demo/sarcathon_demo.db demo/sarcathon_agent.db   # fresh demo workspace (demo files only)
./start_milaan_demo.sh --demo-db
```

Open **http://127.0.0.1:8501/**. The header badge reads **Demo Database**,
and `./status_milaan_demo.sh` prints `database: demo/sarcathon_demo.db`.
The demo workspace starts empty.

Files used (all synthetic, matching each other):

| File | Contents |
| --- | --- |
| `demo/demo_purchase_invoices.csv` | 6 purchase bills; `DEMO-106` has inconsistent tax and is rejected |
| `demo/demo_gstr2b_portal.json` | September GSTR-2B in GST-portal JSON layout (`data.docdata.b2b`) |
| `demo/demo_gstr2b_portal_next.json` | October GSTR-2B in which the supplier reported `DEMO-105` late |

## A. Workspace script

1. **Purchase Invoices → Upload Invoices** → `demo/demo_purchase_invoices.csv`.
   Preview: *5 of 6 rows valid*; `DEMO-106` is shown with its tax error and
   is not imported. (Optional: upload a GSTR-2B file here to show the single
   inline error explaining it belongs on the GSTR-2B page.)
2. **GSTR-2B Import → Choose File** → `demo/demo_gstr2b_portal.json`.
   Preview: *All 5 rows valid*; rows show **Imported**.
3. **Reconciliation → Run reconciliation.** Expected, with values read from
   the stored rows:

   | Invoice | Status | Note |
   | --- | --- | --- |
   | DEMO-101, DEMO-102, DEMO-104 | Matched | differences 0 |
   | DEMO-103 | Amount mismatch | ITC difference ₹300 (₹1,800 billed, ₹1,500 in 2B) |
   | DEMO-105 | Missing from 2B | no 2B values invented |
   | DEMO-999 | In 2B, not in books | 2B-only row |

   Use the Status filter + **Apply filters** to isolate mismatches; filters
   never re-run reconciliation.
4. **Supplier Follow-ups → Prepare drafts.** One draft (Umang Electricals,
   DEMO-105) appears as **Needs review**: no WhatsApp number is on file. Open
   it (⋮), enter a *consenting test number* in **Supplier WhatsApp number**,
   and save. The draft becomes **Pending**; nothing is sent.
5. Tick *"I reviewed this message"* → **Approve Draft** → status
   **In progress**. Under **Delivery**, tick both confirmations and press
   **Simulate send** (a mock delivery record; the modal states that live
   delivery is off). Simulation never marks the follow-up resolved.
6. **Next cycle:** GSTR-2B Import → `demo/demo_gstr2b_portal_next.json`.
   Its one row shows **Check required**: DEMO-105 is dated September but
   was reported in the October return, the late-filing signature.
7. Reopen the follow-up → **Check a later GSTR-2B statement** → select the
   October batch → **Check statement**. Recovery evidence becomes
   **VERIFIED** and the follow-up becomes **Resolved**.

Do not press **Run reconciliation** after step 6: the newest batch has no
fully valid rows, and the app will (correctly) refuse and say why.

## B. WhatsApp agent loop (offline)

```bash
AGENT_DB_PATH=/tmp/milaan_demo_agent.db .venv/bin/python -m agent.simulate --script demo --offline
```

The same `Agent` code runs with a console channel and the disclosed fixture
reader instead of the vision model. It shows: a checksum-verified bill; a
smudged GSTIN answered with a targeted re-ask (never guessed); a GSTIN that
conflicts with the 2B routed to the trader, not the supplier; drafts with the
total at stake and nothing sent before *haan*; and the November 2B marking
3 bills / ₹16,362 **RECOVERED**. Escalation after the chase limit and the
CHANNEL-BLOCKED phone-call script are covered by `test_agent.py`.

## Back to the real workspace

```bash
./stop_milaan_demo.sh
./start_milaan_demo.sh
```

## What the demo proves

* Portal-JSON GSTR-2B parsing with exact amounts, deterministic
  reconciliation, and values/differences that agree with each status.
* Invalid input is rejected and shown, never coerced or partially imported.
* Supplier messages need a number the trader supplies, explicit approval
  and explicit send confirmation; "Resolved" needs later 2B evidence.
* The demo workspace is separate from `milaan.db` and labelled as demo.

## What it does not prove

* Live extraction accuracy (needs an LLM API key; see `EVALUATION.md`).
* Live supplier delivery: WhatsApp Cloud API is not configured, so supplier
  sends are simulated. The WhatsApp Web bridge can only send the fixed test
  message to the configured test number.
* Compatibility with genuine (non-synthetic) GST portal exports, live GSTIN
  registration, or supplier fault.
