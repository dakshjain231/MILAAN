"""Labelled synthetic evaluation for reconciliation and file ingestion.

Every GSTIN, invoice, GSTR-2B row and workbook here is synthetic. No real
taxpayer data, no portal export and no message delivery is involved.

Run:
    .venv/bin/python reconciliation_eval.py
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any

import gstr2b
import reconciliation

_LABEL = "labelled synthetic data; no real taxpayer data"

_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_CSV_HEADER = (
    "supplier_gstin,invoice_number,invoice_date,tax_period,"
    "taxable_value,cgst,sgst,igst,total_gst"
)


def _checksum(first_14: str) -> str:
    total = 0
    for position, character in enumerate(first_14):
        value = _CHARSET.index(character.upper())
        product = value * (1 if position % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _CHARSET[(36 - total % 36) % 36]


def _gstin(first_14: str) -> str:
    return first_14 + _checksum(first_14)


GSTIN_A = _gstin("27AAACS1234A1Z")
GSTIN_B = _gstin("29AAACB5678B1Z")
PERIOD = "2026-03"


def _invoice(
    *,
    invoice_id: int = 1,
    gstin: str | None = GSTIN_A,
    number: str = "INV-1",
    period: str | None = PERIOD,
    invoice_date: str = "2026-03-05",
    taxable: int = 1_250_000,
    cgst: int = 112_500,
    sgst: int = 112_500,
    igst: int = 0,
    cess: int = 0,
    total_gst: int = 225_000,
    status: str = "valid",
) -> reconciliation.InvoiceRecord:
    return reconciliation.InvoiceRecord(
        invoice_id=invoice_id,
        supplier_gstin=gstin,
        invoice_number=number,
        tax_period=period,
        total_gst=total_gst,
        validation_status=status,
        invoice_date=invoice_date,
        taxable_value=taxable,
        cgst=cgst,
        sgst=sgst,
        igst=igst,
        cess=cess,
    )


def _row(
    *,
    row_id: int = 1,
    gstin: str | None = GSTIN_A,
    number: str = "INV-1",
    period: str | None = PERIOD,
    invoice_date: str = "2026-03-05",
    taxable: int = 1_250_000,
    cgst: int = 112_500,
    sgst: int = 112_500,
    igst: int = 0,
    cess: int = 0,
    total_gst: int = 225_000,
    status: str = "valid",
    itc_availability: str | None = None,
    reverse_charge: str | None = None,
) -> reconciliation.Gstr2bRecord:
    return reconciliation.Gstr2bRecord(
        row_id=row_id,
        supplier_gstin=gstin,
        invoice_number=number,
        tax_period=period,
        total_gst=total_gst,
        validation_status=status,
        invoice_date=invoice_date,
        taxable_value=taxable,
        cgst=cgst,
        sgst=sgst,
        igst=igst,
        cess=cess,
        itc_availability=itc_availability,
        reverse_charge=reverse_charge,
    )


@dataclass(frozen=True)
class Scenario:
    id: str
    description: str
    invoices: tuple[reconciliation.InvoiceRecord, ...]
    rows: tuple[reconciliation.Gstr2bRecord, ...]
    expected_status: str
    expected_reason: str | None = None
    expected_method: str | None = None
    expected_auto_match: bool = False
    target: str = "invoice"
    tolerance: int = 0


def _scenarios() -> list[Scenario]:
    return [
        Scenario(
            "exact_match",
            "Exact GSTIN, invoice number, period and every amount agree.",
            (_invoice(),), (_row(),),
            reconciliation.STATUS_MATCHED,
            expected_method=reconciliation.METHOD_EXACT,
            expected_auto_match=True,
        ),
        Scenario(
            "exact_after_normalization",
            "Case and internal whitespace differ; normalized key matches.",
            (_invoice(number="  inv   1001 "),),
            (_row(number="INV 1001"),),
            reconciliation.STATUS_MATCHED,
            expected_method=reconciliation.METHOD_EXACT,
            expected_auto_match=True,
        ),
        Scenario(
            "exact_interstate",
            "IGST-only invoice matches an IGST-only 2B row.",
            (_invoice(cgst=0, sgst=0, igst=225_000),),
            (_row(cgst=0, sgst=0, igst=225_000),),
            reconciliation.STATUS_MATCHED,
            expected_method=reconciliation.METHOD_EXACT,
            expected_auto_match=True,
        ),
        Scenario(
            "gstin_mismatch",
            "Same invoice number and amount, different supplier GSTIN.",
            (_invoice(),), (_row(gstin=GSTIN_B),),
            reconciliation.STATUS_MISSING_FROM_2B,
            expected_reason=reconciliation.REASON_NO_2B_ROW_FOUND,
        ),
        Scenario(
            "period_mismatch",
            "Same GSTIN and invoice number, different tax period.",
            (_invoice(),), (_row(period="2026-04"),),
            reconciliation.STATUS_MISSING_FROM_2B,
            expected_reason=reconciliation.REASON_NO_2B_ROW_FOUND,
        ),
        Scenario(
            "invoice_number_variant",
            "OCR-like O/0 variant must remain review-only.",
            (_invoice(number="INV-1001"),),
            (_row(number="INV-1OO1"),),
            reconciliation.STATUS_NEEDS_REVIEW,
            expected_reason=reconciliation.REASON_NEAR_INVOICE_NUMBER,
            expected_method=reconciliation.METHOD_NEAR_INVOICE,
        ),
        Scenario(
            "duplicate_purchase_invoice",
            "Two books invoices share the same normalized key.",
            (
                _invoice(invoice_id=1, number="INV-DUP"),
                _invoice(invoice_id=2, number="INV-DUP"),
            ),
            (_row(number="INV-DUP"),),
            reconciliation.STATUS_NEEDS_REVIEW,
            expected_reason=reconciliation.REASON_DUPLICATE_PURCHASE_INVOICE,
        ),
        Scenario(
            "duplicate_2b_candidates",
            "Two 2B rows share the same normalized key.",
            (_invoice(),),
            (_row(row_id=1), _row(row_id=2)),
            reconciliation.STATUS_NEEDS_REVIEW,
            expected_reason=reconciliation.REASON_DUPLICATE_GSTR2B_CANDIDATES,
        ),
        Scenario(
            "swapped_components_equal_total",
            "Equal total GST but CGST and SGST are swapped.",
            (_invoice(cgst=200_000, sgst=25_000),),
            (_row(cgst=25_000, sgst=200_000),),
            reconciliation.STATUS_AMOUNT_MISMATCH,
            expected_reason=reconciliation.REASON_COMPONENT_MISMATCH,
        ),
        Scenario(
            "taxable_value_mismatch",
            "Components agree but taxable value differs.",
            (_invoice(taxable=1_250_000),),
            (_row(taxable=1_200_000),),
            reconciliation.STATUS_AMOUNT_MISMATCH,
            expected_reason=reconciliation.REASON_TAXABLE_VALUE_MISMATCH,
        ),
        Scenario(
            "date_mismatch",
            "Components agree but invoice dates differ.",
            (_invoice(invoice_date="2026-03-05"),),
            (_row(invoice_date="2026-03-06"),),
            reconciliation.STATUS_AMOUNT_MISMATCH,
            expected_reason=reconciliation.REASON_DATE_MISMATCH,
        ),
        Scenario(
            "cess_mismatch",
            "Components agree but 2B carries cess.",
            (_invoice(cess=0),),
            (_row(cess=500),),
            reconciliation.STATUS_AMOUNT_MISMATCH,
            expected_reason=reconciliation.REASON_CESS_MISMATCH,
        ),
        Scenario(
            "books_only",
            "Valid books invoice has no 2B counterpart.",
            (_invoice(),), (),
            reconciliation.STATUS_MISSING_FROM_2B,
            expected_reason=reconciliation.REASON_NO_2B_ROW_FOUND,
        ),
        Scenario(
            "twob_only",
            "Usable 2B row has no books invoice.",
            (), (_row(),),
            reconciliation.STATUS_IN_2B_NOT_IN_BOOKS,
            expected_reason=reconciliation.REASON_IN_2B_NOT_IN_BOOKS,
            expected_method=reconciliation.METHOD_ORPHAN_2B,
            target="orphan",
        ),
        Scenario(
            "itc_unavailable",
            "2B marks ITC unavailable; never auto-confirmed as claimable.",
            (_invoice(),), (_row(itc_availability="N"),),
            reconciliation.STATUS_ITC_UNAVAILABLE,
            expected_reason=reconciliation.REASON_ITC_NOT_AVAILABLE,
        ),
        Scenario(
            "reverse_charge",
            "2B marks reverse charge; routed to ITC review.",
            (_invoice(),), (_row(reverse_charge="Y"),),
            reconciliation.STATUS_ITC_UNAVAILABLE,
            expected_reason=reconciliation.REASON_REVERSE_CHARGE,
        ),
        Scenario(
            "date_period_inconsistent",
            "Invoice date falls outside its tax period.",
            (_invoice(invoice_date="2026-04-02"),),
            (_row(invoice_date="2026-04-02"),),
            reconciliation.STATUS_NEEDS_REVIEW,
            expected_reason=reconciliation.REASON_DATE_PERIOD_INCONSISTENT,
            expected_method=reconciliation.METHOD_EXACT,
        ),
        Scenario(
            "amount_only_candidate",
            "Different invoice number but one same-GSTIN/period amount match.",
            (_invoice(number="A-1"),),
            (_row(number="B-1"),),
            reconciliation.STATUS_NEEDS_REVIEW,
            expected_reason=reconciliation.REASON_CANDIDATE_NOT_EXACT_INVOICE_NUMBER,
            expected_method=reconciliation.METHOD_CANDIDATE_AMOUNT,
        ),
        Scenario(
            "ambiguous_amount_candidates",
            "Two same-GSTIN/period amount candidates must not be selected.",
            (_invoice(number="A-1"),),
            (_row(row_id=1, number="B-1"), _row(row_id=2, number="C-1")),
            reconciliation.STATUS_NEEDS_REVIEW,
            expected_reason=reconciliation.REASON_AMBIGUOUS_CANDIDATES,
        ),
        Scenario(
            "not_eligible_invoice",
            "Invalid purchase invoice is excluded from matching.",
            (_invoice(status="invalid"),), (_row(),),
            reconciliation.STATUS_NOT_ELIGIBLE,
            expected_reason=reconciliation.REASON_INVOICE_INVALID,
            expected_method=reconciliation.METHOD_INELIGIBLE,
        ),
        Scenario(
            "default_tolerance_zero",
            "A one-paise total difference fails at the default tolerance.",
            (_invoice(total_gst=225_001),),
            (_row(total_gst=225_000),),
            reconciliation.STATUS_AMOUNT_MISMATCH,
            expected_reason=(
                reconciliation.REASON_AMOUNT_DIFFERENCE_BEYOND_TOLERANCE
            ),
        ),
        Scenario(
            "explicit_tolerance",
            "The same one-paise difference matches when tolerance is explicit.",
            (_invoice(total_gst=225_001),),
            (_row(total_gst=225_000),),
            reconciliation.STATUS_MATCHED,
            expected_reason=reconciliation.REASON_AMOUNT_WITHIN_TOLERANCE,
            expected_method=reconciliation.METHOD_EXACT_TOLERANCE,
            expected_auto_match=True,
            tolerance=1,
        ),
    ]


def _select_result(
    scenario: Scenario, results: list[reconciliation.MatchResult]
) -> reconciliation.MatchResult | None:
    if scenario.target == "orphan":
        for result in results:
            if result.invoice_id is None:
                return result
        return None
    if scenario.invoices:
        for result in results:
            if result.invoice_id == scenario.invoices[0].invoice_id:
                return result
    return None


def run_scenarios() -> list[dict]:
    outcomes = []
    for scenario in _scenarios():
        config = reconciliation.ReconciliationConfig(
            amount_tolerance_paise=scenario.tolerance
        )
        results = reconciliation.reconcile_batch(
            list(scenario.invoices), list(scenario.rows), config=config
        )
        result = _select_result(scenario, results)
        actual_status = result.match_status if result else "NO_RESULT"
        actual_reasons = list(result.reason_codes) if result else []
        actual_method = result.match_method if result else ""
        reason_ok = (
            scenario.expected_reason is None
            or scenario.expected_reason in actual_reasons
        )
        method_ok = (
            scenario.expected_method is None
            or scenario.expected_method == actual_method
        )
        status_ok = scenario.expected_status == actual_status
        outcomes.append({
            "id": scenario.id,
            "description": scenario.description,
            "target": scenario.target,
            "expected_status": scenario.expected_status,
            "actual_status": actual_status,
            "expected_reason": scenario.expected_reason,
            "actual_reasons": actual_reasons,
            "expected_method": scenario.expected_method,
            "actual_method": actual_method,
            "expected_auto_match": scenario.expected_auto_match,
            "actual_auto_match": actual_status == reconciliation.STATUS_MATCHED,
            "status_ok": status_ok,
            "reason_ok": reason_ok,
            "method_ok": method_ok,
            "pass": status_ok and reason_ok and method_ok,
        })
    return outcomes


def _csv(rows: list[list[Any]], header: str = _CSV_HEADER) -> bytes:
    lines = [header]
    for row in rows:
        lines.append(",".join(str(value) for value in row))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _portal_workbook(
    headers: list[str], rows: list[list[Any]]
) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "B2B"
    sheet.append(["GSTR-2B B2B export (synthetic)"])
    sheet.append([])
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


_PORTAL_HEADERS = [
    "GSTIN of supplier",
    "Invoice Number",
    "Invoice Date",
    "Taxable Value",
    "Integrated Tax",
    "Central Tax",
    "State/UT Tax",
    "Cess",
    "GSTR-1/IFF/GSTR-5 Period",
    "ITC Availability",
    "Reason",
    "Supply Attract Reverse Charge",
    "Invoice Type",
    "IRN",
]

_PORTAL_ROW = [
    GSTIN_A, "INV-P1", "2026-03-05", "12500.00", "0.00", "1125.00",
    "1125.00", "0.00", "2026-03", "Y", "", "N", "R", "SYNTHETIC-IRN",
]


@dataclass(frozen=True)
class FileScenario:
    id: str
    description: str
    file_name: str
    content: bytes
    expectation: str
    accepted: int = 0
    rejected: int = 0
    parser_kind: str | None = None
    source_row: int | None = None
    error_contains: str | None = None


def _file_scenarios() -> list[FileScenario]:
    valid_row = [
        GSTIN_A, "INV-1", "2026-03-05", PERIOD, "12500.00", "1125.00",
        "1125.00", "0.00", "2250.00",
    ]
    title_csv = (
        "GSTR-2B synthetic export\n"
        "Generated for testing only\n"
        + _CSV_HEADER + "\n"
        + ",".join(valid_row) + "\n"
    ).encode("utf-8")
    return [
        FileScenario(
            "canonical_csv",
            "Documented canonical CSV is accepted.",
            "canonical.csv", _csv([valid_row]), "parsed",
            accepted=1, rejected=0,
        ),
        FileScenario(
            "title_rows_csv",
            "Leading title rows are skipped and the header is detected.",
            "titles.csv", title_csv, "parsed",
            accepted=1, rejected=0,
        ),
        FileScenario(
            "portal_xlsx",
            "Portal B2B workbook is detected with sheet and source row.",
            "portal.xlsx",
            _portal_workbook(_PORTAL_HEADERS, [_PORTAL_ROW]),
            "parsed",
            accepted=1, rejected=0, parser_kind="portal_b2b", source_row=4,
        ),
        FileScenario(
            "ambiguous_portal_headers",
            "Two portal columns mapping to IGST are rejected.",
            "ambiguous.xlsx",
            _portal_workbook(
                ["GSTIN of supplier", "Invoice Number", "Invoice Date",
                 "Taxable Value", "Integrated Tax", "IGST", "Central Tax",
                 "State/UT Tax"],
                [_PORTAL_ROW[:4] + ["0.00", "0.00", "1125.00", "1125.00"]],
            ),
            "error", error_contains="ambiguous",
        ),
        FileScenario(
            "incomplete_headers",
            "Missing required portal column is rejected with its name.",
            "incomplete.xlsx",
            _portal_workbook(
                ["GSTIN of supplier", "Invoice Number", "Invoice Date",
                 "Taxable Value", "Central Tax", "State/UT Tax"],
                [[GSTIN_A, "INV-1", "2026-03-05", "12500.00",
                  "1125.00", "1125.00"]],
            ),
            "error", error_contains="missing required column",
        ),
        FileScenario(
            "duplicate_column",
            "Duplicate canonical alias is rejected, not silently chosen.",
            "duplicate.csv",
            _csv([valid_row + ["INV-1"]], header=_CSV_HEADER + ",Invoice No"),
            "error", error_contains="duplicate column",
        ),
        FileScenario(
            "invalid_money_row",
            "Row-level invalid money is rejected but the file continues.",
            "bad-money.csv",
            _csv([[GSTIN_A, "INV-BAD", "2026-03-05", PERIOD, "abc",
                   "1125.00", "1125.00", "0.00", "2250.00"]]),
            "parsed", accepted=0, rejected=1,
        ),
        FileScenario(
            "empty_file",
            "Empty file fails cleanly.",
            "empty.csv", b"", "error", error_contains="empty",
        ),
        FileScenario(
            "pdf_rejected",
            "PDF is rejected; OCR/PDF support is not claimed.",
            "statement.pdf", b"%PDF-1.4 synthetic",
            "error", error_contains="PDF GSTR-2B is not supported",
        ),
        FileScenario(
            "unsupported_extension",
            "Unsupported extension is rejected.",
            "statement.txt", b"not a spreadsheet",
            "error", error_contains="unsupported file type",
        ),
    ]


def run_file_scenarios() -> list[dict]:
    outcomes = []
    for scenario in _file_scenarios():
        actual: dict[str, Any] = {
            "id": scenario.id,
            "description": scenario.description,
            "file": scenario.file_name,
            "expectation": scenario.expectation,
        }
        try:
            parsed = gstr2b.parse_gstr2b(
                scenario.content, scenario.file_name
            )
        except ValueError as exc:
            actual.update({
                "outcome": "error",
                "error": str(exc),
                "accepted": None,
                "rejected": None,
                "parser_kind": None,
                "source_row": None,
            })
            passed = (
                scenario.expectation == "error"
                and scenario.error_contains is not None
                and scenario.error_contains in str(exc)
            )
        else:
            source_row = parsed.rows[0].source_row if parsed.rows else None
            actual.update({
                "outcome": "parsed",
                "error": None,
                "accepted": parsed.accepted_rows,
                "rejected": parsed.rejected_rows,
                "parser_kind": parsed.parser_kind,
                "source_row": source_row,
            })
            passed = (
                scenario.expectation == "parsed"
                and parsed.accepted_rows == scenario.accepted
                and parsed.rejected_rows == scenario.rejected
                and (
                    scenario.parser_kind is None
                    or parsed.parser_kind == scenario.parser_kind
                )
                and (
                    scenario.source_row is None
                    or source_row == scenario.source_row
                )
            )
        actual["pass"] = passed
        outcomes.append(actual)
    return outcomes


def run_evaluation() -> dict:
    scenarios = run_scenarios()
    files = run_file_scenarios()

    tp = sum(
        1 for item in scenarios
        if item["expected_auto_match"] and item["actual_auto_match"]
    )
    fp = sum(
        1 for item in scenarios
        if not item["expected_auto_match"] and item["actual_auto_match"]
    )
    fn = sum(
        1 for item in scenarios
        if item["expected_auto_match"] and not item["actual_auto_match"]
    )
    tn = sum(
        1 for item in scenarios
        if not item["expected_auto_match"] and not item["actual_auto_match"]
    )
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    unresolved = sum(
        1 for item in scenarios
        if item["actual_status"] in {
            reconciliation.STATUS_NEEDS_REVIEW,
            reconciliation.STATUS_MISSING_FROM_2B,
            reconciliation.STATUS_ITC_UNAVAILABLE,
            reconciliation.STATUS_NOT_ELIGIBLE,
        }
    )
    return {
        "data": _LABEL,
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "metrics": {
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "true_negatives": tn,
            "false_matches": fp,
            "precision": precision,
            "recall": recall,
            "unresolved_cases": unresolved,
            "scenario_pass": (
                f"{sum(1 for item in scenarios if item['pass'])}"
                f"/{len(scenarios)}"
            ),
        },
        "file_ingestion": {
            "scenario_count": len(files),
            "passed": sum(1 for item in files if item["pass"]),
            "scenarios": files,
        },
    }


def main() -> int:
    report = run_evaluation()
    print(json.dumps(report, indent=2, sort_keys=True))
    metrics = report["metrics"]
    ok = (
        metrics["false_matches"] == 0
        and report["file_ingestion"]["passed"]
        == report["file_ingestion"]["scenario_count"]
        and metrics["scenario_pass"].split("/")[0]
        == metrics["scenario_pass"].split("/")[1]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
