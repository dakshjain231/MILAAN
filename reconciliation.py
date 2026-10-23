"""Deterministic invoice-to-GSTR-2B reconciliation (Task 5).

Pure module: no database access, no LLM, no network. It compares validated
purchase invoices against the accepted rows of one GSTR-2B import batch
and returns an auditable result for every invoice plus unmatched 2B rows.

Matching policy (documented in README):

1. Only invoices whose stored ``validation_status`` is ``"valid"`` are
   eligible. Everything else is reported as ``NOT_ELIGIBLE`` with a
   specific reason code; invalid or unreviewed invoices are never
   silently treated as valid.
2. Only GSTR-2B rows with ``validation_status == "valid"`` are used as
   candidates. Rejected rows stay visible in the import audit trail but
   never become matches.
3. Match keys are the normalized supplier GSTIN, the normalized invoice
   number and the normalized tax period:
   - GSTIN: whitespace removed, uppercased.
   - Invoice number: leading/trailing whitespace stripped, internal
     whitespace runs collapsed to one space, then uppercased. No other
     characters are removed for the exact key, because punctuation may
     be meaningful.
   - Tax period: whitespace stripped; ``YYYY-MM`` and ``YYYY/MM`` are
     canonicalized to ``YYYY-MM``. Records from different tax periods
     or GSTINs are never compared.
4. Exact-match-first: same key and single candidate. ``MATCHED`` requires
   equal taxable value (when both sides have it), equal IGST/CGST/SGST,
   equal cess (missing cess counts as 0), and equal total GST within
   the configured paise tolerance (default zero). A total-GST-only
   agreement is never enough when a material component, taxable value,
   cess, date, or ITC-availability difference remains.
5. Invoice-date vs tax-period inconsistency, ITC marked unavailable, and
   reverse-charge Y are never auto-confirmed as claimable matches.
6. Near-number review: punctuation, leading zeros, and likely OCR
   variants such as O/0 are surfaced as ``NEEDS_REVIEW``. They are never
   auto-accepted and never used to generate supplier drafts.
7. If no exact-number candidate exists, a constrained candidate search
   considers rows with the same GSTIN and tax period whose total GST is
   within tolerance. A single such row is ``NEEDS_REVIEW``; multiple
   rows are ``AMBIGUOUS_CANDIDATES``. No candidate at all is
   ``MISSING_FROM_2B``.
8. Unmatched valid GSTR-2B rows become ``IN_2B_NOT_IN_BOOKS``. Presence
   in 2B does not prove ITC is claimable.
9. Duplicate purchase invoices or duplicate 2B rows after normalization
   are ``NEEDS_REVIEW``; no candidate is selected.

Amount differences are signed ``invoice.total_gst - row.total_gst`` in
integer paise. A missing row proves only that this batch has no usable
counterpart: it does NOT prove supplier non-filing, ITC eligibility or
recoverability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STATUS_MATCHED = "MATCHED"
STATUS_AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
STATUS_MISSING_FROM_2B = "MISSING_FROM_2B"
STATUS_IN_2B_NOT_IN_BOOKS = "IN_2B_NOT_IN_BOOKS"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_NOT_ELIGIBLE = "NOT_ELIGIBLE"
STATUS_ITC_UNAVAILABLE = "ITC_UNAVAILABLE"

MATCH_STATUSES = (
    STATUS_MATCHED,
    STATUS_AMOUNT_MISMATCH,
    STATUS_MISSING_FROM_2B,
    STATUS_IN_2B_NOT_IN_BOOKS,
    STATUS_NEEDS_REVIEW,
    STATUS_NOT_ELIGIBLE,
    STATUS_ITC_UNAVAILABLE,
)

METHOD_EXACT = "exact"
METHOD_EXACT_TOLERANCE = "exact_tolerance"
METHOD_CANDIDATE_AMOUNT = "candidate_amount"
METHOD_NEAR_INVOICE = "near_invoice"
METHOD_ORPHAN_2B = "in_2b_not_in_books"
METHOD_NONE = "none"
METHOD_INELIGIBLE = "ineligible"

MATCH_METHODS = (
    METHOD_EXACT,
    METHOD_EXACT_TOLERANCE,
    METHOD_CANDIDATE_AMOUNT,
    METHOD_NEAR_INVOICE,
    METHOD_ORPHAN_2B,
    METHOD_NONE,
    METHOD_INELIGIBLE,
)

REASON_INVOICE_INVALID = "INVOICE_INVALID"
REASON_INVOICE_NEEDS_REVIEW = "INVOICE_NEEDS_REVIEW"
REASON_INVOICE_PENDING = "INVOICE_PENDING"
REASON_INVOICE_NOT_VALID = "INVOICE_NOT_VALID"
REASON_MISSING_MATCH_KEY = "MISSING_MATCH_KEY"
REASON_DUPLICATE_PURCHASE_INVOICE = "DUPLICATE_PURCHASE_INVOICE"
REASON_DUPLICATE_GSTR2B_CANDIDATES = "DUPLICATE_GSTR2B_CANDIDATES"
REASON_AMBIGUOUS_CANDIDATES = "AMBIGUOUS_CANDIDATES"
REASON_CANDIDATE_NOT_EXACT_INVOICE_NUMBER = "CANDIDATE_NOT_EXACT_INVOICE_NUMBER"
REASON_NEAR_INVOICE_NUMBER = "NEAR_INVOICE_NUMBER"
REASON_AMOUNT_WITHIN_TOLERANCE = "AMOUNT_WITHIN_TOLERANCE"
REASON_AMOUNT_DIFFERENCE_BEYOND_TOLERANCE = "AMOUNT_DIFFERENCE_BEYOND_TOLERANCE"
REASON_TAXABLE_VALUE_MISMATCH = "TAXABLE_VALUE_MISMATCH"
REASON_COMPONENT_MISMATCH = "COMPONENT_MISMATCH"
REASON_CESS_MISMATCH = "CESS_MISMATCH"
REASON_DATE_MISMATCH = "DATE_MISMATCH"
REASON_DATE_PERIOD_INCONSISTENT = "DATE_PERIOD_INCONSISTENT"
REASON_ITC_NOT_AVAILABLE = "ITC_NOT_AVAILABLE"
REASON_REVERSE_CHARGE = "REVERSE_CHARGE"
REASON_NO_2B_ROW_FOUND = "NO_2B_ROW_FOUND"
REASON_IN_2B_NOT_IN_BOOKS = "IN_2B_NOT_IN_BOOKS"

VALIDATION_VALID = "valid"

_TAX_PERIOD_RE = re.compile(r"^(\d{4})[-/](\d{2})$")
_NEAR_KEEP = re.compile(r"[^A-Z0-9]")


@dataclass(frozen=True)
class ReconciliationConfig:
    """Matching configuration. Tolerance is integer paise, default zero."""

    amount_tolerance_paise: int = 0

    def __post_init__(self) -> None:
        value = self.amount_tolerance_paise
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(
                "amount_tolerance_paise must be a non-negative integer "
                f"(paise), got {value!r}"
            )


@dataclass(frozen=True)
class InvoiceRecord:
    invoice_id: int
    supplier_gstin: str | None
    invoice_number: str
    tax_period: str | None
    total_gst: int
    validation_status: str
    invoice_date: str | None = None
    taxable_value: int | None = None
    cgst: int | None = None
    sgst: int | None = None
    igst: int | None = None
    cess: int | None = None


@dataclass(frozen=True)
class Gstr2bRecord:
    row_id: int
    supplier_gstin: str | None
    invoice_number: str
    tax_period: str | None
    total_gst: int | None
    validation_status: str
    invoice_date: str | None = None
    taxable_value: int | None = None
    cgst: int | None = None
    sgst: int | None = None
    igst: int | None = None
    cess: int | None = None
    itc_availability: str | None = None
    itc_reason: str | None = None
    reverse_charge: str | None = None
    document_type: str | None = None


@dataclass(frozen=True)
class MatchResult:
    invoice_id: int | None
    match_status: str
    match_method: str
    reason_codes: tuple[str, ...]
    gstr2b_row_id: int | None = None
    amount_difference_paise: int | None = None
    invoice_number: str = ""
    supplier_gstin: str | None = None
    tax_period: str | None = None
    invoice_total_gst_paise: int = 0
    gstr2b_invoice_number: str | None = None
    gstr2b_total_gst_paise: int | None = None


def normalize_gstin(value: str | None) -> str | None:
    """Uppercase and remove whitespace; None/blank stays None."""
    if value is None:
        return None
    text = "".join(str(value).split()).upper()
    return text or None


def normalize_invoice_number(value: str | None) -> str | None:
    """Trim, collapse internal whitespace and uppercase.

    No characters are removed: punctuation is preserved so distinct
    invoice numbers cannot be merged by normalization.
    """
    if value is None:
        return None
    text = " ".join(str(value).split()).upper()
    return text or None


def near_invoice_fingerprint(value: str | None) -> str | None:
    """Constrained OCR/punctuation fingerprint; never used as an auto-match."""
    normalized = normalize_invoice_number(value)
    if normalized is None:
        return None
    text = (
        normalized.replace("O", "0")
        .replace("I", "1")
        .replace("L", "1")
    )
    text = _NEAR_KEEP.sub("", text).lstrip("0")
    return text or "0"


def normalize_tax_period(value: str | None) -> str | None:
    """Canonicalize ``YYYY-MM`` / ``YYYY/MM`` to ``YYYY-MM``.

    Returns None for blank, malformed or impossible month values.
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    match = _TAX_PERIOD_RE.fullmatch(text)
    if match is None:
        return None
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return f"{match.group(1)}-{match.group(2)}"


def date_in_tax_period(invoice_date: str | None, tax_period: str | None) -> bool | None:
    """True when YYYY-MM of the date equals the period; None if either is missing."""
    period = normalize_tax_period(tax_period)
    if not invoice_date or period is None:
        return None
    text = str(invoice_date).strip()
    if len(text) < 7:
        return None
    return text[:7] == period


def _ineligible_reason(status: str) -> str:
    return {
        "invalid": REASON_INVOICE_INVALID,
        "needs_review": REASON_INVOICE_NEEDS_REVIEW,
        "pending": REASON_INVOICE_PENDING,
    }.get(status, REASON_INVOICE_NOT_VALID)


def _result(
    invoice: InvoiceRecord | None,
    status: str,
    method: str,
    reasons: tuple[str, ...],
    *,
    row: Gstr2bRecord | None = None,
    difference: int | None = None,
) -> MatchResult:
    if invoice is None:
        if row is None:
            raise ValueError("orphan 2B results require a GSTR-2B row")
        return MatchResult(
            invoice_id=None,
            match_status=status,
            match_method=method,
            reason_codes=reasons,
            gstr2b_row_id=row.row_id,
            amount_difference_paise=difference,
            invoice_number=row.invoice_number,
            supplier_gstin=normalize_gstin(row.supplier_gstin),
            tax_period=normalize_tax_period(row.tax_period),
            invoice_total_gst_paise=row.total_gst or 0,
            gstr2b_invoice_number=row.invoice_number,
            gstr2b_total_gst_paise=row.total_gst,
        )
    return MatchResult(
        invoice_id=invoice.invoice_id,
        match_status=status,
        match_method=method,
        reason_codes=reasons,
        gstr2b_row_id=row.row_id if row is not None else None,
        amount_difference_paise=difference,
        invoice_number=invoice.invoice_number,
        supplier_gstin=normalize_gstin(invoice.supplier_gstin),
        tax_period=normalize_tax_period(invoice.tax_period),
        invoice_total_gst_paise=invoice.total_gst,
        gstr2b_invoice_number=row.invoice_number if row is not None else None,
        gstr2b_total_gst_paise=row.total_gst if row is not None else None,
    )


def _cess(value: int | None) -> int:
    return 0 if value is None else value


def _component_reasons(
    invoice: InvoiceRecord,
    row: Gstr2bRecord,
    *,
    tolerance: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if (
        invoice.taxable_value is not None
        and row.taxable_value is not None
        and invoice.taxable_value != row.taxable_value
    ):
        reasons.append(REASON_TAXABLE_VALUE_MISMATCH)
    for field in ("cgst", "sgst", "igst"):
        left = getattr(invoice, field)
        right = getattr(row, field)
        if left is not None and right is not None and left != right:
            reasons.append(REASON_COMPONENT_MISMATCH)
            break
    if _cess(invoice.cess) != _cess(row.cess) and (
        invoice.cess is not None or row.cess is not None
    ):
        if invoice.cess is not None and row.cess is not None:
            reasons.append(REASON_CESS_MISMATCH)
        elif row.cess not in (None, 0) or invoice.cess not in (None, 0):
            reasons.append(REASON_CESS_MISMATCH)
    if (
        invoice.invoice_date
        and row.invoice_date
        and invoice.invoice_date != row.invoice_date
    ):
        reasons.append(REASON_DATE_MISMATCH)
    if date_in_tax_period(invoice.invoice_date, invoice.tax_period) is False:
        reasons.append(REASON_DATE_PERIOD_INCONSISTENT)
    if date_in_tax_period(row.invoice_date, row.tax_period) is False:
        reasons.append(REASON_DATE_PERIOD_INCONSISTENT)
    difference = invoice.total_gst - (row.total_gst or 0)
    if abs(difference) > tolerance:
        reasons.append(REASON_AMOUNT_DIFFERENCE_BEYOND_TOLERANCE)
    elif difference != 0:
        reasons.append(REASON_AMOUNT_WITHIN_TOLERANCE)
    availability = (row.itc_availability or "").upper()
    if availability in {"N", "NO", "UNAVAILABLE"}:
        reasons.append(REASON_ITC_NOT_AVAILABLE)
    reverse = (row.reverse_charge or "").upper()
    if reverse in {"Y", "YES"}:
        reasons.append(REASON_REVERSE_CHARGE)
    return tuple(dict.fromkeys(reasons))


def _usable_rows(
    gstr2b_rows: list[Gstr2bRecord],
) -> list[tuple[Gstr2bRecord, str, str, str]]:
    usable: list[tuple[Gstr2bRecord, str, str, str]] = []
    for row in gstr2b_rows:
        if row.validation_status != VALIDATION_VALID:
            continue
        gstin = normalize_gstin(row.supplier_gstin)
        number = normalize_invoice_number(row.invoice_number)
        period = normalize_tax_period(row.tax_period)
        if gstin is None or number is None or period is None:
            continue
        if row.total_gst is None:
            continue
        usable.append((row, gstin, number, period))
    return usable


def reconcile_invoices(
    invoices: list[InvoiceRecord],
    gstr2b_rows: list[Gstr2bRecord],
    *,
    config: ReconciliationConfig | None = None,
) -> list[MatchResult]:
    """Reconcile invoices against one batch's rows, deterministically.

    Results are returned in invoice_id order, one per supplied invoice.
    Source records are only read, never modified.
    """
    config = config or ReconciliationConfig()
    tolerance = config.amount_tolerance_paise

    results: dict[int, MatchResult] = {}
    eligible: list[tuple[InvoiceRecord, str, str, str]] = []

    for invoice in sorted(invoices, key=lambda item: item.invoice_id):
        if invoice.validation_status != VALIDATION_VALID:
            results[invoice.invoice_id] = _result(
                invoice,
                STATUS_NOT_ELIGIBLE,
                METHOD_INELIGIBLE,
                (_ineligible_reason(invoice.validation_status),),
            )
            continue
        gstin = normalize_gstin(invoice.supplier_gstin)
        number = normalize_invoice_number(invoice.invoice_number)
        period = normalize_tax_period(invoice.tax_period)
        if gstin is None or number is None or period is None:
            results[invoice.invoice_id] = _result(
                invoice,
                STATUS_NEEDS_REVIEW,
                METHOD_NONE,
                (REASON_MISSING_MATCH_KEY,),
            )
            continue
        eligible.append((invoice, gstin, number, period))

    keyed_invoices: dict[tuple[str, str, str], list[InvoiceRecord]] = {}
    for invoice, gstin, number, period in eligible:
        keyed_invoices.setdefault((gstin, number, period), []).append(invoice)
    duplicate_invoice_ids: set[int] = set()
    for group in keyed_invoices.values():
        if len(group) > 1:
            for invoice in group:
                duplicate_invoice_ids.add(invoice.invoice_id)
                results[invoice.invoice_id] = _result(
                    invoice,
                    STATUS_NEEDS_REVIEW,
                    METHOD_NONE,
                    (REASON_DUPLICATE_PURCHASE_INVOICE,),
                )

    usable_rows = _usable_rows(gstr2b_rows)

    exact_index: dict[tuple[str, str, str], list[Gstr2bRecord]] = {}
    near_index: dict[tuple[str, str, str], list[Gstr2bRecord]] = {}
    for row, gstin, number, period in usable_rows:
        exact_index.setdefault((gstin, number, period), []).append(row)
        fingerprint = near_invoice_fingerprint(number)
        if fingerprint is not None:
            near_index.setdefault((gstin, fingerprint, period), []).append(row)

    for invoice, gstin, number, period in eligible:
        if invoice.invoice_id in duplicate_invoice_ids:
            continue
        key = (gstin, number, period)
        exact_rows = exact_index.get(key, [])

        if len(exact_rows) > 1:
            results[invoice.invoice_id] = _result(
                invoice,
                STATUS_NEEDS_REVIEW,
                METHOD_NONE,
                (REASON_DUPLICATE_GSTR2B_CANDIDATES,),
            )
            continue

        if len(exact_rows) == 1:
            row = exact_rows[0]
            difference = invoice.total_gst - row.total_gst
            reasons = _component_reasons(invoice, row, tolerance=tolerance)
            blocking = [
                code for code in reasons
                if code not in {REASON_AMOUNT_WITHIN_TOLERANCE}
            ]
            if REASON_ITC_NOT_AVAILABLE in reasons or REASON_REVERSE_CHARGE in reasons:
                status = STATUS_ITC_UNAVAILABLE
                method = METHOD_EXACT if difference == 0 else METHOD_EXACT
                results[invoice.invoice_id] = _result(
                    invoice, status, method, reasons, row=row, difference=difference
                )
                continue
            review_codes = {
                REASON_DATE_PERIOD_INCONSISTENT,
            }
            if any(code in review_codes for code in reasons):
                results[invoice.invoice_id] = _result(
                    invoice,
                    STATUS_NEEDS_REVIEW,
                    METHOD_EXACT,
                    reasons,
                    row=row,
                    difference=difference,
                )
                continue
            if not blocking:
                method = METHOD_EXACT if difference == 0 else METHOD_EXACT_TOLERANCE
                results[invoice.invoice_id] = _result(
                    invoice,
                    STATUS_MATCHED,
                    method,
                    reasons,
                    row=row,
                    difference=difference,
                )
            else:
                results[invoice.invoice_id] = _result(
                    invoice,
                    STATUS_AMOUNT_MISMATCH,
                    METHOD_EXACT,
                    reasons,
                    row=row,
                    difference=difference,
                )
            continue

        fingerprint = near_invoice_fingerprint(number)
        near_rows = [
            row for row in near_index.get((gstin, fingerprint, period), [])
            if normalize_invoice_number(row.invoice_number) != number
        ]
        if len(near_rows) == 1:
            row = near_rows[0]
            results[invoice.invoice_id] = _result(
                invoice,
                STATUS_NEEDS_REVIEW,
                METHOD_NEAR_INVOICE,
                (REASON_NEAR_INVOICE_NUMBER,),
                row=row,
                difference=invoice.total_gst - row.total_gst,
            )
            continue
        if len(near_rows) > 1:
            results[invoice.invoice_id] = _result(
                invoice,
                STATUS_NEEDS_REVIEW,
                METHOD_NONE,
                (REASON_AMBIGUOUS_CANDIDATES, REASON_NEAR_INVOICE_NUMBER),
            )
            continue

        candidates = [
            row
            for row, row_gstin, _row_number, row_period in usable_rows
            if row_gstin == gstin
            and row_period == period
            and abs(invoice.total_gst - row.total_gst) <= tolerance
        ]
        if len(candidates) == 1:
            row = candidates[0]
            results[invoice.invoice_id] = _result(
                invoice,
                STATUS_NEEDS_REVIEW,
                METHOD_CANDIDATE_AMOUNT,
                (REASON_CANDIDATE_NOT_EXACT_INVOICE_NUMBER,),
                row=row,
                difference=invoice.total_gst - row.total_gst,
            )
        elif len(candidates) > 1:
            results[invoice.invoice_id] = _result(
                invoice,
                STATUS_NEEDS_REVIEW,
                METHOD_NONE,
                (REASON_AMBIGUOUS_CANDIDATES,),
            )
        else:
            results[invoice.invoice_id] = _result(
                invoice,
                STATUS_MISSING_FROM_2B,
                METHOD_NONE,
                (REASON_NO_2B_ROW_FOUND,),
            )

    return [results[invoice.invoice_id] for invoice in
            sorted(invoices, key=lambda item: item.invoice_id)]


def reconcile_batch(
    invoices: list[InvoiceRecord],
    gstr2b_rows: list[Gstr2bRecord],
    *,
    config: ReconciliationConfig | None = None,
) -> list[MatchResult]:
    """Two-way reconciliation: invoice results plus unmatched 2B rows."""
    invoice_results = reconcile_invoices(invoices, gstr2b_rows, config=config)
    used_row_ids = {
        result.gstr2b_row_id
        for result in invoice_results
        if result.gstr2b_row_id is not None
    }
    orphans: list[MatchResult] = []
    for row, _gstin, _number, _period in _usable_rows(gstr2b_rows):
        if row.row_id in used_row_ids:
            continue
        orphans.append(
            _result(
                None,
                STATUS_IN_2B_NOT_IN_BOOKS,
                METHOD_ORPHAN_2B,
                (REASON_IN_2B_NOT_IN_BOOKS,),
                row=row,
                difference=None,
            )
        )
    orphans.sort(key=lambda item: item.gstr2b_row_id or 0)
    return invoice_results + orphans
