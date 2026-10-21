# MILAAN evaluation

Measured on a deterministic synthetic benchmark using the real MILAAN
services and a fresh temporary SQLite database. The benchmark contains no
real taxpayer data, no GST portal exports and no WhatsApp delivery.

Reproduce:

```bash
.venv/bin/python benchmark.py
.venv/bin/python -m unittest test_benchmark -v
```

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

- **Extraction accuracy:** no OCR or invoice-extraction pipeline exists, so
  this metric is `null`, not `0` and not `100%`.
- **Real GST portal compatibility:** no authorized genuine portal export is
  in the repository. The supported input is the normalized schema
  documented in `gstr2b.py`.
- **Live GSTIN validity, ITC eligibility or supplier non-filing:** GSTIN
  checks are syntax/checksum only. `MISSING_FROM_2B` means only that no
  usable counterpart exists in the selected batch.
- **Real WhatsApp delivery:** it remains disabled/simulated. No message was
  sent during this evaluation.
- **Real-world generalizability:** the benchmark was designed alongside the
  rules, is small, and is not a blind holdout. Its labels test the stated
  policy; they do not establish production accuracy.

## Limitations and next validation step

The largest unverified area is input compatibility with actual authorized
GST portal exports. The next step is to add anonymized, legally obtained
sample layouts and a consenting pilot workflow, then repeat this evaluation
on a genuinely held-out real-world set. Until then, MILAAN should be
described as a deterministic normalized-file prototype, not a
portal-integrated production system.
