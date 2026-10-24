# MILAAN synthetic judge demo

A repeatable, fully synthetic walkthrough of the primary Flask workspace:

1. Use an isolated demo database with four synthetic suppliers and five bills.
2. Import a normalized GSTR-2B CSV with one rejected row.
3. Reconcile the bills against that batch.
4. Preview eligibility, then generate two supplier drafts.
5. Approve one draft for delivery review and leave the other in `DRAFT`.
6. Verify that no external message was sent.

The demo uses only `demo/milaan_demo.db`. It never touches `milaan.db`,
`agent_data/milaan_agent.db`, or any real supplier conversation. Demo mode
also disables the WhatsApp Web bridge, Cloud API, webhook and inbound agent.

## One-command start

From the project root (stop any instance already using port 8501 first):

```bash
./run_milaan.sh --stop
./run_milaan.sh --demo
```

The launcher seeds `demo/milaan_demo.db` if it does not exist, then opens the
same primary UI at **http://127.0.0.1:8501**. The page shows an
**Isolated synthetic demo** banner with the database path.

To reset before a new walkthrough:

```bash
./run_milaan.sh --stop
.venv/bin/python demo/setup_demo.py
./run_milaan.sh --demo
```

Setup resets only the demo database and rewrites `demo/DEMO_GSTR2B.csv`.
Expected setup result: 4 synthetic suppliers, 5 invoices, 4 valid and
1 invalid; no import, reconciliation, draft or delivery.

## Demo script

### 1. Show seeded data

Open **Bills**.

- Invoices: **5**.
- Validation: **4 valid, 0 needs review, 1 invalid**.
- `DEMO-INVALID-001` has an invalid date (`2026-02-30`), so it cannot be
  matched or used for follow-up.
- No GSTR-2B batches, reconciliation runs or follow-up drafts yet.

Show **Suppliers** for the four synthetic master records. Phone numbers are
masked in the UI.

Talking point: validation is deterministic field/checksum/arithmetic
checking, not a claim of live GST registration verification.

### 2. Import GSTR-2B

Open **GSTR-2B**, upload `demo/DEMO_GSTR2B.csv`, and preview it first.

- Expected preview: **3 rows, 2 accepted, 1 rejected**.
- The rejected row (`DEMO-BAD-ROW`) has a bad GSTIN and negative GST.
- Confirm the import. Expected: **Batch #1: 2 accepted, 1 rejected**.

Talking point: the supported input is MILAAN's normalized schema, not an
arbitrary real portal export. Rejected rows are preserved and shown, never
silently coerced.

### 3. Reconcile

Open **Reconciliation**.

- Select batch #1.
- Leave tolerance at **0 paise**.
- Click **Reconcile batch**.

Expected run:

- **1 matched**
- **1 amount mismatch**
- **2 missing from 2B**
- **0 needs review**
- **1 not eligible**
- **5 bills compared**

Evidence table:

| Invoice | Expected result |
| --- | --- |
| `DEMO-EXACT-001` | `MATCHED` |
| `DEMO-AMOUNT-001` | `AMOUNT_MISMATCH`, difference `-50.00` |
| `DEMO-MISSING-001` | `MISSING_FROM_2B` |
| `DEMO-MISSING-002` | `MISSING_FROM_2B` |
| `DEMO-INVALID-001` | `NOT_ELIGIBLE` |

Use the row's **⋮** action on a mismatch or missing bill to compare the
purchase bill and the linked GSTR-2B row, including reason codes and amount
differences. Use the **Status** filter (then **Apply filters**) to show only
open mismatches.

Talking point: `MISSING_FROM_2B` means only that this batch has no usable
counterpart. It does not prove supplier non-filing, ITC eligibility or
recoverability.

### 4. Preview and generate follow-ups

Open **Supplier follow-ups**.

- Choose batch #1 in **Eligibility preview**.
- Click **Preview eligibility**. Nothing is written.
- Expected: **2 eligible invoices across 2 supplier groups**, with the
  matched, amount-mismatched and invalid bills listed under
  **Excluded invoices and reasons**.
- Expand **Preview draft wording** to show the neutral message text.
- Click **Generate drafts**. Expected: **2 drafts created**. Both start as
  `DRAFT`; nothing is sent.

Show the draft status history: `CREATED -> DRAFT` with a note that no message
was sent.

### 5. Approve one draft, leave one clean draft

- Select the Meridian Components draft and click **Review**.
- Show the neutral wording and supporting invoice evidence.
- Click **Approve for delivery review** and confirm.
- Expected: the draft becomes `APPROVED`; no message has been sent.
- Leave the other draft in `DRAFT` to demonstrate the pre-approval state.

Do not create a delivery in the judge demo. The approval snapshot is the end
of the guaranteed workflow.

## What the demo proves

- End-to-end deterministic import, reconciliation, eligibility preview,
  draft creation and approval.
- Exact, amount-mismatch, missing, invalid and review paths are explicit.
- Invalid rows and invoices are excluded, not coerced.
- Older reconciliation runs keep their own invoice-level evidence.
- Drafts require human approval and no external send occurs.
- The demo database is isolated from operational and agent data and uses
  synthetic values only.

## What it does not prove

- Compatibility with genuine GST portal export layouts.
- Live GSTIN validity, ITC eligibility or supplier fault.
- OCR or arbitrary invoice extraction (no such pipeline exists).
- Real WhatsApp delivery.
- Production-scale or real-world accuracy; see `EVALUATION.md`.
