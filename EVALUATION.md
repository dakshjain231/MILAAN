# MILAAN evaluation

Measured on a deterministic synthetic benchmark using the real MILAAN
services and a fresh temporary SQLite database. The benchmark contains no
real taxpayer data, no GST portal exports and no WhatsApp delivery.

Reproduce:

```bash
.venv/bin/python benchmark.py
.venv/bin/python -m unittest test_benchmark -v
```

## Deterministic reconciliation and file-ingestion evaluation

This is a separate, labelled synthetic benchmark for the reconciliation
policy and GSTR-2B file handling. Every GSTIN, invoice, 2B row and workbook
is synthetic; no real taxpayer data or supplier message is involved.

Reproduce:

```bash
.venv/bin/python reconciliation_eval.py
.venv/bin/python -m pytest -q test_evaluation.py test_report_workbook.py test_gstr2b_portal.py
```

| Metric | Result |
| --- | ---: |
| Labelled reconciliation scenarios | 22 / 22 passed |
| False matches | 0 |
| False positives / false negatives | 0 / 0 |
| Precision / recall on labelled matches | 1.00 / 1.00 |
| Unresolved cases routed to review/missing | 12 |
| File-ingestion scenarios | 10 / 10 passed |

Covered cases include exact and normalized matches, interstate IGST,
GSTIN/period differences, duplicate books and duplicate 2B candidates,
swapped CGST/SGST with equal total, taxable/date/cess differences, explicit
tolerance, books-only and 2B-only rows, ITC unavailability, and ineligible
invoices. File scenarios cover canonical CSV with title rows, portal CSV
fallback, portal XLSX source-row preservation, ambiguous portal columns,
incomplete headers, duplicate aliases, bad money, empty files, PDF rejection
and unsupported extensions.

The accountant workbook is generated from persisted run evidence and then
reopened to verify sheet names, row counts, totals, hashes and metadata.
Untrusted cells are escaped so they cannot become spreadsheet formulas.
Excel forbids `/` in sheet names, so the requested
`Duplicate/Near-match Review` sheet is named
`Duplicate Near-match Review` in the file. All other required sheets use
their exact names.

These results validate the stated matching policy on known perturbations.
They do not establish accuracy on unaudited real portal exports; that remains
the limitation described below.

### Verification record (2026-10-23)

- Python: `438 passed, 122 subtests passed` (`pytest -q`, after removing obsolete Streamlit UI tests).
- Reconciliation evaluation: `22/22` scenarios, `10/10` file scenarios,
  `0` false matches.
- WhatsApp service: `18/18` tests passed; `npm run check` passed.
- HTTP smoke test on a temporary database: `/health`, `/`, `/api/state`,
  `/api/export/reconciliation.xlsx` and
  `/api/reconciliation/<run_id>/workbook.xlsx` returned `200`; an unknown
  run returned `404`. Both downloaded workbooks reopened and validated with
  no problems. No supplier message was sent.

## What is being measured

Benchmark dataset:

- 3 synthetic suppliers, including one without a phone number.
- 15 purchase invoices.
- 14 normalized GSTR-2B rows, all accepted.
- Cases: 3 exact, 2 amount mismatches, 1 invalid purchase invoice,
  5 ambiguous/review cases, and 4 purchase invoices with no usable 2B
  counterpart (2 absent, 1 different 2B GSTIN, 1 different 2B tax period).

The positive class is a valid purchase invoice whose normalized GSTIN,
invoice number and tax period identify exactly one usable GSTR-2B row and
whose total GST equals that row exactly at tolerance 0. The expected status
for that case is `MATCHED`.

Headline precision/recall uses 9 clear cases: 3 positives and 6 clear
negatives (2 amount mismatches and 4 missing cases). The 5 ambiguous cases
and 1 invalid record are excluded from headline precision/recall and
reported separately, because the correct product outcome for those cases is
review or exclusion, not a binary match decision.

Precision = TP / (TP + FP). Recall = TP / (TP + FN).

## Matching results

| Metric | Result |
| --- | ---: |
| Headline cases | 9 |
| True positives | 3 |
| False positives | 0 |
| False negatives | 0 |
| True negatives | 6 |
| Precision | 1.00 |
| Recall | 1.00 |
| Exact-match rate on the 3 exact cases | 1.00 |
| Amount mismatches detected | 2 / 2 |
| Invalid records excluded | 1 / 1 |
| Ambiguous cases routed to `NEEDS_REVIEW` | 5 / 5 |
| Ambiguous cases auto-accepted | 0 |

These are synthetic benchmark results. They are not evidence of 100%
accuracy on real GSTR-2B exports, real supplier data or other taxpayer
profiles.

## Baseline comparison

The baseline matches only normalized supplier GSTIN + invoice number and
ignores tax period, duplicate candidates and amount differences. It is
evaluated on the same 9 headline cases.

| Metric | MILAAN | Baseline |
| --- | ---: | ---: |
| True positives | 3 | 3 |
| False positives | 0 | 3 |
| False negatives | 0 | 0 |
| True negatives | 6 | 3 |
| Precision | 1.00 | 0.50 |
| Recall | 1.00 | 1.00 |
| Ambiguous cases auto-accepted | 0 / 5 | 3 / 5 |

The baseline's extra false positives are the two amount mismatches and the
tax-period difference. Its recall is unchanged because it does not reject
any of the three exact matches.

## Follow-up and process results

| Metric | Result |
| --- | ---: |
| Follow-up inclusion/exclusion decisions correct | 15 / 15 |
| Clean invoice rows drafted | 3 / 3 |
| Draft groups generated | 2 |
| Clean `DRAFT` groups | 1 |
| `NEEDS_REVIEW` groups (phone not on file) | 1 |
| Phone-missing case routed to review | 1 / 1 |
| Identical file imports | 2 distinct batches |
| Reconciliation runs for the first batch | 2 |
| Repeated run results identical | Yes |
| Duplicate batch has a separate run | Yes |
| External sends during benchmark | 0 |

The clean draft groups three unambiguous missing invoices for one synthetic
supplier. The second group requires review because that supplier has no
phone number on file. No contact details were invented.

## Invalid-data and exception behavior

- The 15-invoice benchmark has 1 invalid purchase invoice (invalid date).
  It was excluded from matching and follow-up.
- Parser failure behavior is tested separately in `test_realworld`:
  malformed XLSX, invalid UTF-8, empty files, header-only files, missing
  columns, duplicate rows, bad GSTINs, bad dates, bad tax periods, bad
  money and tax-total mismatches.
- Duplicate file import is explicit: the identical file creates a separate
  batch and a separate reconciliation run. Results for the same batch are
  reproducible.

## Not measured

- **Extraction accuracy in this benchmark:** this reconciliation benchmark
  starts from structured invoice rows, so it measures no extraction. The
  WhatsApp agent does have a vision extraction pipeline (`agent/extract.py`);
  its evaluation is section 2 of the agent evaluation below, and so far only
  a 3-image partial run exists.
- **Real GST portal compatibility:** the web importer parses GST-portal
  GSTR-2B JSON (`data.docdata.b2b`) and portal Excel exports
  (`gstr2b_portal.py`), and the agent has its own portal JSON reader
  (`agent/twob.py`). Both are tested only on synthetic portal-shaped files;
  no authorized genuine portal export is in the repository. Printed-PDF
  GSTR-2B is not supported.
- **Live GSTIN validity, ITC eligibility or supplier non-filing:** GSTIN
  checks are syntax/checksum only. `MISSING_FROM_2B` means only that no
  usable counterpart exists in the selected batch.
- **Real WhatsApp delivery:** it remains disabled/simulated. No message was
  sent during this evaluation.
- **Real-world generalizability:** the benchmark was designed alongside the
  rules, is small, and is not a blind holdout. Its labels test the stated
  policy; they do not establish production accuracy.

## Limitations and next validation step

The largest unverified areas are input compatibility with actual authorized
GST portal exports and extraction accuracy on real bill photographs. The next
step is to add anonymized, legally obtained portal exports and consented,
redacted bills, then repeat both evaluations on a genuinely held-out set.
Until then, MILAAN's portal parsing should be described as tested on
synthetic portal-shaped files, not validated on real exports.


---

# Agent evaluation (Round 2)

## 1. Diagnosis: who should be messaged? (`python -m agent.eval.diagnosis_eval`)

* **Data:** 20 synthetic traders × 40 October invoices, so 800 per seed, with seeded true causes.
* **Real code path:** each trader's data goes through `store` and then `diagnose.run()`.
* **Seeds:** we ran 3 seeds (7, 11, 23).

Correct action per cause:
* **CHASE:** non-filing, amount mismatch.
* **ASK_TRADER:** GSTIN conflict, our own GSTIN misread.
* **NO_ACTION:** everything else.

| Metric (seed 7 / 11 / 23) | B0 status quo | B2 exact-string matcher | MILAAN |
|---|---:|---:|---:|
| Correct action | 0.78 / 0.76 / 0.76 | 0.76 / 0.75 / 0.77 | **0.99 / 0.97 / 0.98** |
| Chase precision | n/a | 0.40 / 0.43 / 0.45 | **0.93 / 0.87 / 0.90** |
| Chase recall | 0 | 1.00 | **1.00** |
| Chase drafts to innocent suppliers | 0 | 143 / 154 / 145 | **10 / 22 / 18** |
| Messages on a misread GSTIN (to the wrong business) | 0 | 25 / 21 / 21 | **0** |
| Chases before an unconfirmed GSTIN was resolved | 0 | 26 / 25 / 21 | **0** |
| At-risk GST identified | 0% | 100% | 100% |

**Where MILAAN fails.** All of its innocent-supplier drafts come from one deliberately held-out
hard case, `qrmp_thin`: a quarterly filer with only one period of 2B history. The QRMP rule
needs at least two periods, so MILAAN chases them. The trader can say "X is quarterly", which
fixes it, and the trader's approval gate still stands before any message leaves.

**Caveat.** The same team wrote the rules and the generator. This shows the policy is
implemented correctly on known perturbations; it is not a blind real-world test.

**Reproduced on 2026-10-22.** Re-running seeds 7, 11 and 23 gave output identical to the three
files in `agent_data/eval/` (`diagnosis_results.json`, `diagnosis_seed11.json`,
`diagnosis_seed23.json`). Every cell of the table above matches those files.

## 2. Extraction (`python -m agent.eval.extraction_eval --run`, needs an API key)

The test set has 49 synthetic rendered bills:
* 10 photo-damage types × 4 bills: blur, low light, noise, JPEG, crumpled, rotated, partial
  crop, smudged GSTIN, smudged total, clean
* 3 bills with a misprinted GSTIN
* 3 bills with a wrong tax line printed on the paper
* 3 non-bills

The safety metric is **unsafe pass**: a wrong or unreadable GSTIN or tax amount that got past
every gate without being flagged. The script also reports:
* per-field accuracy
* re-ask burden
* latency
* tokens and cost per bill

Results go to `agent_data/eval/extraction_results.json`. *Numbers are filled in only from an
actual run; none are claimed here.*

### Partial run, 2026-10-22 (`--run --limit 3`)

* **Model:** `gemini-3.5-flash`, double read on.
* **Sample:** only 3 of the 49 images. `--limit` takes the first files alphabetically, so all
  three are `arith_error_00` to `arith_error_02`: clean photos of bills with a wrong tax line
  printed on the paper. The photo-damage types, misprinted GSTINs and non-bills were not run.
* **Why so few:** the Gemini free tier allows 20 requests per model per day, and this eval makes
  two requests per bill.

| Metric | Result (n = 3) |
|---|---:|
| Images / errors | 3 / 0 |
| Bill vs. not-a-bill correct | 1.00 |
| Field accuracy: GSTIN, invoice no., date, total GST, taxable value | 1.00 each |
| Unsafe passes | 0 |
| Bills held for the trader (all on `total_gst`) | 3 / 3 |
| Auto-accepted | 0 |
| False flags | 0 |
| Average seconds per bill | 14.64 |
| Average tokens in / out per bill | 2782 / 2135 |

On these three bills the gate did its job: the printed tax line was wrong, and each bill was
held for the trader instead of being accepted. This is one category with n = 3, not an
accuracy estimate. The file also holds `cost_usd_per_bill`, but the script computes it from its
default token prices, which were not checked against `gemini-3.5-flash` pricing, so no cost is
claimed here. The run itself used the free tier.

## 3. B1 single-shot LLM baseline (`python -m agent.eval.b1_single_shot --runs 3`, needs an API key)

The model gets all 9 demo bills, the October 2B and the supplier history in one call, and
returns an action per bill. MILAAN gets 9/9 on the same month (`test_agent`). The n is small,
and we say so.

### Run, 2026-10-22 (`--runs 1`)

* **Model:** `gemini-3.5-flash`. Source: `agent_data/eval/b1_results.json`.
* `--runs 3` failed on its first call with HTTP 503 (Gemini overloaded) and wrote no file. It was
  re-run with `--runs 1` to stay inside the free-tier quota.

| Metric | B1 (1 run) |
|---|---:|
| Correct action | 8 / 9 |
| Chase recall | 1.00 |
| Messages to innocent suppliers | 0 |
| Chased JPS/0977 before its GSTIN conflict was confirmed | Yes |
| Bills missing from the answer | 0 |
| Tokens in / out | 12083 / 3910 |

The one miss is the unsafe kind. B1 chose CHASE for JPS/0977, whose GSTIN conflicts with the 2B,
where the correct action is to ask the trader first.

**About the MILAAN side.** `test_agent.AgentFlowTests.test_full_demo_cycle` passes. It asserts
that JPS/0977 waits for the trader and that nothing reaches a supplier before "haan", plus the
final state of most of the other bills, but not every one of the 9. It writes no eval file, so
the 9/9 above is not a figure from `agent_data/eval/`.

## Cost per completed trader-month

Every LLM call's token usage is written to the trace (`usage`). Cost per bill comes from the
extraction eval. Cost per month ≈ bills × cost per bill, plus planner turns, plus Twilio
messages (sandbox: free).

## Round 2 reproduction record (2026-10-24)

Re-run on a temporary database for each script; committed result files were
not overwritten.

| Check | Command | Result |
| --- | --- | --- |
| Diagnosis eval, B0 / B2 / MILAAN, seeds 7, 11, 23 | `python -m agent.eval.diagnosis_eval --seed N --out /tmp/...` | Output identical to `agent_data/eval/diagnosis_*.json` for all three seeds |
| Reconciliation policy eval | `python reconciliation_eval.py` | 22 / 22 scenarios, 10 / 10 file scenarios, 0 false matches, precision 1.00, recall 1.00 |
| Matching benchmark vs exact-key baseline | `python benchmark.py` | MILAAN precision 1.00 / recall 1.00; baseline precision 0.50 / recall 1.00 |
| Agent demo cycle, offline fixture reader | `python -m agent.simulate --script demo --offline` | Completed: targeted GSTIN re-ask, GSTIN conflict routed to the trader, approval gate, next-month 2B → 3 bills / ₹16,362 RECOVERED |
| B1 single-shot LLM baseline | `python -m agent.eval.b1_single_shot` | **Not re-run**: no LLM API key on this machine. The only result is the 1-run file from 2026-10-22 above |
| Extraction eval (49 images) | `python -m agent.eval.extraction_eval --run` | **Not re-run**: no LLM API key. Only the 3-image partial run exists |
| Workspace demo, browser | `node scripts/demo_e2e.js` (see DEMO.md) | 25 / 25 checks: import → reconcile → approve → simulated send → next-cycle 2B → Resolved; no console errors |
| WhatsApp Web bridge live send | Settings → Send test message (to the configured, self-owned test number) | **Not verified.** 2 attempts; `whatsapp-web.js` 1.34.7 returned no message id both times, so the outcome is unknown (now recorded as `UNKNOWN`, never retried automatically). WhatsApp Cloud API is not configured, so no supplier delivery was attempted |

**Gap against the Round 1 evaluation plan.** The plan described about 150 bill
photographs labelled independently by both authors with agreement reported,
and a mismatch-detection precision/recall on paired bill-set and 2B cases.
What exists is 49 synthetic rendered bills (single-labelled by construction),
the synthetic diagnosis benchmark (800 invoices per seed) and the
reconciliation scenarios above. Task-completion rate and rupees recovered are
reported for the scripted demo only, not as a measured rate. Cost per
trader-month is instrumented (token usage is written to the trace) but has
not been measured on a full run.
