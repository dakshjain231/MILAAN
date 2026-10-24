"""Inbound WhatsApp documents: classify, import, reconcile, draft.

This is the receiving half of the WhatsApp Web bridge. The Node service
(``whatsapp_service/``) queues attachments from allowlisted senders; this
module polls that queue over loopback, decides which MILAAN parser owns the
file, imports it, and — once both halves of a tax period are present — runs
reconciliation and prepares supplier follow-up drafts.

Deliberate boundaries:

* **Nothing here sends.** No function in this module calls a delivery or
  approval path, so an incoming message can never produce an outgoing one.
  Drafts are created in DRAFT / NEEDS_REVIEW and still require the existing
  two-confirmation approval flow before anything can be delivered.
* **Nothing here overwrites.** Imports go through
  ``db.save_invoice_import`` / ``db.save_gstr2b_import``, which skip rows
  whose natural key already exists. Duplicate detection is layered: the
  WhatsApp message id (exact re-delivery), the file SHA-256 (the same file
  sent again) and then the per-row natural key.
* **Nothing is guessed.** A file whose schema is ambiguous, unreadable or
  unsupported is recorded with a precise reason instead of being routed to
  a parser that would misread it. Formats that would need a service MILAAN
  does not have (OCR for photos, speech-to-text for voice notes, a PDF text
  layer) report that blocker verbatim.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import db
import gstr2b
import invoice_import

CHANNEL_WHATSAPP_WEB = "whatsapp_web"

KIND_PURCHASE_INVOICE = "purchase_invoice"
KIND_GSTR2B = "gstr2b"
KIND_UNSUPPORTED = "unsupported"

# Must stay in sync with SUPPORTED_EXTENSIONS in whatsapp_service/src/inbound.js.
SUPPORTED_EXTENSIONS = ("csv", "xlsx", "json")

STATUS_IMPORTED = "imported"
STATUS_DUPLICATE = "duplicate"
STATUS_REJECTED = "rejected"
STATUS_UNSUPPORTED = "unsupported"
STATUS_RECORDED = "recorded"
STATUS_FAILED = "failed"

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_STORAGE_DIRNAME = "inbound_media"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Columns only a purchase register has. A GSTR-2B statement never carries
# all three, and a purchase register contains every GSTR-2B column, so these
# are what make the two schemas distinguishable.
_PURCHASE_ONLY_COLUMNS = frozenset({"supplier_name", "gst_rate", "invoice_total"})

# Why each rejected format is rejected, in the operator's words. Photos,
# voice notes and PDFs name the missing service rather than implying the
# feature exists: MILAAN has no OCR, no speech-to-text and no PDF text
# extraction configured for this channel.
REASON_DETAILS = {
    "UNSUPPORTED_PDF": (
        "PDF attachments are not parsed: no PDF text-extraction or OCR service is "
        "configured for MILAAN. Send the purchase register as CSV/XLSX, or the "
        "GSTR-2B as the GST portal's JSON/Excel export."
    ),
    "UNSUPPORTED_LEGACY_XLS": (
        "Legacy .xls workbooks are not parsed. Save the file as .xlsx or CSV and "
        "send it again."
    ),
    "UNSUPPORTED_IMAGE": (
        "Photos of invoices are not read on this channel: no vision or OCR service "
        "is configured for the WhatsApp Web bridge (LLM_API_KEY is unset). Send a "
        "CSV/XLSX purchase register instead."
    ),
    "UNSUPPORTED_AUDIO": (
        "Voice notes are not transcribed: no speech-to-text service is configured "
        "(GEMINI_API_KEY is unset), so Hindi or English audio cannot be turned into "
        "invoice data. Send the data as a CSV/XLSX file instead."
    ),
    "UNSUPPORTED_VIDEO": "Video messages are never processed as accounting data.",
    "UNSUPPORTED_EXTENSION": (
        "This file type is not parsed. Supported attachments are CSV, XLSX and JSON."
    ),
    "UNKNOWN_FILE_TYPE": (
        "The attachment has no recognisable file name or type, so it was not parsed. "
        "Resend it with a .csv, .xlsx or .json file name."
    ),
    "TEXT_MESSAGE": (
        "Text message recorded for the audit trail; it carried no file to import."
    ),
    "EMPTY_FILE": "The attachment contained no data, so nothing was imported.",
    "FILE_TOO_LARGE": (
        "The attachment is larger than the inbound size limit "
        "(MILAAN_WA_INBOUND_MAX_BYTES) and was not imported."
    ),
    "DOWNLOAD_FAILED": (
        "The attachment could not be downloaded from WhatsApp, so nothing was "
        "imported. It stays queued for the next poll."
    ),
    "AMBIGUOUS_SCHEMA": (
        "This file matches neither template cleanly: it mixes purchase-register and "
        "GSTR-2B columns, so MILAAN will not guess which one it is. Send the "
        "purchase-invoice template or an unmodified GSTR-2B export."
    ),
    "UNRECOGNISED_SCHEMA": (
        "No purchase-invoice or GSTR-2B column layout was recognised in this file, "
        "so nothing was imported."
    ),
    "DUPLICATE_MESSAGE": (
        "This exact WhatsApp message was already processed; nothing was imported "
        "again."
    ),
    "DUPLICATE_FILE": (
        "A file with identical contents was already imported, so no new records "
        "were created."
    ),
    "NO_VALID_ROWS": (
        "Every row in this file failed validation, so nothing was imported."
    ),
}


class InboundError(RuntimeError):
    """Base error for inbound intake; messages are safe to show."""


class InboundBridgeError(InboundError):
    """The local bridge could not be reached or answered unusably."""


def reason_detail(code: str, fallback: str | None = None) -> str:
    return REASON_DETAILS.get(code) or fallback or "This message was not imported."


# --- configuration ---------------------------------------------------------

@dataclass(frozen=True)
class InboundConfig:
    """Which senders may deliver files here, and where the files land."""

    enabled: bool
    senders: tuple[str, ...]
    storage_dir: Path
    max_bytes: int

    def sender_allowed(self, value: str | None) -> bool:
        digits = db.normalize_phone_digits(value)
        if digits is None:
            return False
        return digits in self.senders

    def problems(self) -> list[str]:
        problems: list[str] = []
        if not self.enabled:
            problems.append("inbound intake is off (MILAAN_WA_INBOUND_ENABLED)")
        if not self.senders:
            problems.append(
                "no inbound sender is configured (MILAAN_WA_INBOUND_SENDERS, "
                "MILAAN_WA_ALLOWED_RECIPIENTS or MILAAN_WA_TEST_RECIPIENT)"
            )
        if self.max_bytes <= 0:
            problems.append("MILAAN_WA_INBOUND_MAX_BYTES must be positive")
        return problems

    @property
    def ready(self) -> bool:
        return not self.problems()

    def safe_summary(self) -> dict:
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            "senders_masked": [_mask(value) for value in self.senders],
            "max_bytes": self.max_bytes,
            "storage_dir": str(self.storage_dir),
            "problems": self.problems(),
            "supported_extensions": list(SUPPORTED_EXTENSIONS),
        }


def _mask(value: str | None) -> str | None:
    digits = db.normalize_phone_digits(value)
    if digits is None:
        return None
    return "…" + digits[-4:] if len(digits) > 4 else "…"


def _env_bool(value: str | None, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in _TRUE_VALUES


def _parse_senders(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    parts = [piece.strip() for piece in str(raw).replace(";", ",").split(",")]
    digits: list[str] = []
    for piece in parts:
        if not piece:
            continue
        normalized = db.normalize_phone_digits(piece)
        if normalized is not None and normalized not in digits:
            digits.append(normalized)
    return tuple(digits)


def load_inbound_config(
    environ: Mapping[str, str] | None = None,
) -> InboundConfig:
    """Read inbound settings from the environment.

    Senders fall back to the outbound allowlist and then to the test
    recipient, so the pilot number is the only one whose files are read
    unless an operator deliberately lists others. An unset allowlist means
    "nobody", never "anyone".
    """
    env = os.environ if environ is None else environ
    senders = (
        _parse_senders(env.get("MILAAN_WA_INBOUND_SENDERS"))
        or _parse_senders(env.get("MILAAN_WA_ALLOWED_RECIPIENTS"))
        or _parse_senders(env.get("MILAAN_WA_TEST_RECIPIENT"))
    )
    configured_dir = (env.get("MILAAN_WA_INBOUND_DIR") or "").strip()
    storage_dir = (
        Path(configured_dir).expanduser()
        if configured_dir
        else db.PROJECT_ROOT / DEFAULT_STORAGE_DIRNAME
    )
    if not storage_dir.is_absolute():
        storage_dir = db.PROJECT_ROOT / storage_dir

    raw_max = (env.get("MILAAN_WA_INBOUND_MAX_BYTES") or "").strip()
    max_bytes = DEFAULT_MAX_BYTES
    if raw_max:
        try:
            max_bytes = int(raw_max)
        except ValueError:
            max_bytes = DEFAULT_MAX_BYTES

    return InboundConfig(
        enabled=_env_bool(env.get("MILAAN_WA_INBOUND_ENABLED"), True),
        senders=senders,
        storage_dir=storage_dir,
        max_bytes=max_bytes,
    )


# --- classification --------------------------------------------------------

@dataclass(frozen=True)
class Classification:
    kind: str
    reason: str
    detail: str

    @property
    def supported(self) -> bool:
        return self.kind in (KIND_PURCHASE_INVOICE, KIND_GSTR2B)


def _unsupported(reason: str) -> Classification:
    return Classification(KIND_UNSUPPORTED, reason, reason_detail(reason))


def _extension_reason(extension: str) -> str:
    if extension == "pdf":
        return "UNSUPPORTED_PDF"
    if extension in ("xls", "xlsm"):
        return "UNSUPPORTED_LEGACY_XLS"
    if extension in ("jpg", "jpeg", "png", "heic", "webp", "gif", "bmp", "tif", "tiff"):
        return "UNSUPPORTED_IMAGE"
    if extension in ("ogg", "oga", "opus", "mp3", "m4a", "wav", "amr", "aac"):
        return "UNSUPPORTED_AUDIO"
    if extension in ("mp4", "mov", "3gp", "avi", "mkv"):
        return "UNSUPPORTED_VIDEO"
    return "UNSUPPORTED_EXTENSION"


def _detected_columns(headers: Sequence[Any]) -> set[str]:
    """Canonical column names recognised in one header row."""
    found: set[str] = set()
    for header in headers:
        name = gstr2b.normalize_header(header)
        if not name:
            continue
        # The purchase-invoice aliases are a superset of the GSTR-2B ones.
        canonical = invoice_import.COLUMN_ALIASES.get(
            name, gstr2b.COLUMN_ALIASES.get(name, name)
        )
        found.add(canonical)
    return found


def _best_header_columns(headers: list[Any], data_rows: list[list[Any]]) -> set[str]:
    """Columns from the most header-like row, allowing leading title rows.

    GST portal exports put a title above the real header, so the first row is
    not always the header. This mirrors what the real parsers do when they
    locate the canonical header, but never raises: classification only needs
    the best guess at which schema the file uses.
    """
    best: set[str] = set()
    for row in [headers, *data_rows[:30]]:
        detected = _detected_columns(row if isinstance(row, list) else [row])
        if len(detected & _ALL_KNOWN_COLUMNS) > len(best & _ALL_KNOWN_COLUMNS):
            best = detected
    return best


_ALL_KNOWN_COLUMNS = frozenset(invoice_import.COLUMN_ALIASES.values()) | frozenset(
    gstr2b.COLUMN_ALIASES.values()
)


def classify_document(
    content: bytes, file_name: str, *, mime_type: str | None = None
) -> Classification:
    """Decide which MILAAN parser owns this file, by its columns.

    The file name is never the deciding factor: a purchase register called
    ``gstr2b.csv`` is still a purchase register. A file that could plausibly
    be either is reported as ambiguous rather than routed to one of them.
    """
    if not content:
        return _unsupported("EMPTY_FILE")

    name = (file_name or "").strip()
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if not extension:
        return _unsupported("UNKNOWN_FILE_TYPE")
    if extension not in SUPPORTED_EXTENSIONS:
        return _unsupported(_extension_reason(extension))

    # Portal JSON is not a flat table; only the GSTR-2B parser reads it.
    if extension == "json":
        return Classification(KIND_GSTR2B, "OK_GSTR2B", "GSTR-2B JSON export.")

    try:
        headers, data_rows, _ = gstr2b.read_tabular_file(content, name)
    except ValueError as exc:
        return Classification(KIND_UNSUPPORTED, "UNREADABLE_FILE", str(exc))

    detected = _best_header_columns(headers, data_rows)
    purchase_only = detected & _PURCHASE_ONLY_COLUMNS
    has_gstr2b_columns = set(gstr2b.REQUIRED_COLUMNS).issubset(detected)

    if purchase_only == _PURCHASE_ONLY_COLUMNS:
        return Classification(
            KIND_PURCHASE_INVOICE, "OK_PURCHASE_INVOICE",
            "Purchase register (supplier name, GST rate and invoice total present).",
        )
    if purchase_only and has_gstr2b_columns:
        # Only some purchase-only columns, but a complete GSTR-2B layout:
        # the file genuinely reads as either template, so refuse rather than
        # misroute it into the wrong table.
        return _unsupported("AMBIGUOUS_SCHEMA")
    if purchase_only:
        # A purchase register with columns missing. Route it to the invoice
        # parser so the operator gets its precise missing-column list.
        return Classification(
            KIND_PURCHASE_INVOICE, "OK_PURCHASE_INVOICE_PARTIAL",
            "Incomplete purchase register; the invoice parser will validate it.",
        )
    if has_gstr2b_columns:
        return Classification(
            KIND_GSTR2B, "OK_GSTR2B", "GSTR-2B statement column layout.",
        )
    if detected & _ALL_KNOWN_COLUMNS:
        # Some recognised columns, but neither schema is complete. Let the
        # GSTR-2B parser (which also handles portal exports) decide, and
        # report its own message if it cannot read the file.
        return Classification(
            KIND_GSTR2B, "OK_GSTR2B_PARTIAL",
            "Possible GSTR-2B export; the GSTR-2B parser will validate it.",
        )
    return _unsupported("UNRECOGNISED_SCHEMA")


# --- secure storage --------------------------------------------------------

def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def safe_file_name(file_name: str | None) -> str:
    """A sender-supplied name reduced to a harmless base name."""
    raw = str(file_name or "").replace("\\", "/")
    base = raw.rsplit("/", 1)[-1]
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("._")
    return cleaned[:120] or "attachment"


def store_document(
    content: bytes, file_name: str | None, *, config: InboundConfig
) -> Path:
    """Write an inbound attachment to the storage directory, owner-only.

    The stored name is derived from the content hash, never from the sender's
    file name, so a crafted name (``../../etc/passwd``, an absolute path, a
    shell metacharacter) cannot influence where the bytes land. Identical
    content is written once.
    """
    directory = config.storage_dir
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        # A directory MILAAN does not own (a mount, a shared volume) is left
        # as the operator configured it; the file mode below still applies.
        pass

    digest = sha256_hex(content)
    extension = safe_file_name(file_name).rsplit(".", 1)[-1].lower()
    suffix = f".{extension}" if extension in SUPPORTED_EXTENSIONS else ""
    path = (directory / f"{digest}{suffix}").resolve()
    if path.parent != directory.resolve():
        raise InboundError("refusing to store an attachment outside the inbound directory")
    if not path.exists():
        # Create owner-only, then write: the bytes are never briefly readable.
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(handle, content)
        finally:
            os.close(handle)
    return path


# --- the automatic workflow ------------------------------------------------

def _overlapping_periods(batch_id: int) -> list[str]:
    batch_periods = set(db.get_gstr2b_batch_periods(batch_id))
    book_periods = set(db.get_invoice_tax_periods())
    return sorted(batch_periods & book_periods)


def run_auto_workflow(
    *, gstr2b_import_id: int | None, actor: str = "whatsapp-inbound"
) -> dict:
    """Reconcile one GSTR-2B batch and prepare follow-up drafts.

    Runs only when the batch and the purchase register actually share a tax
    period, so a February statement is never reconciled against March books.
    Every refusal is reported with a reason code instead of a silent no-op,
    and nothing here approves or sends anything.
    """
    outcome: dict[str, Any] = {
        "ran": False,
        "reason": None,
        "gstr2b_import_id": gstr2b_import_id,
        "periods": [],
        "run_id": None,
        "drafts_created": 0,
        "drafts_existing": 0,
        "draft_ids": [],
        "verifications": 0,
        "detail": None,
    }
    if gstr2b_import_id is None:
        outcome["reason"] = "NO_MATCHING_GSTR2B_BATCH"
        outcome["detail"] = (
            "No imported GSTR-2B batch covers a tax period present in the "
            "purchase register, so there is nothing to reconcile yet."
        )
        return outcome

    periods = _overlapping_periods(gstr2b_import_id)
    outcome["periods"] = periods
    if not periods:
        outcome["reason"] = "NO_INVOICES_FOR_PERIOD"
        outcome["detail"] = (
            "This GSTR-2B batch and the purchase register have no tax period in "
            "common, so reconciliation was not started."
        )
        return outcome

    try:
        run = db.reconcile_gstr2b_import(gstr2b_import_id)
    except ValueError as exc:
        outcome["reason"] = "RECONCILIATION_NOT_POSSIBLE"
        outcome["detail"] = str(exc)
        return outcome

    outcome["ran"] = True
    outcome["run_id"] = run["run_id"]
    outcome["reason"] = "RECONCILED"
    outcome["counts"] = {
        key: value for key, value in run.items() if key.endswith("_count")
    }

    # Follow-up drafts. "Nothing to follow up" is a normal result, not a
    # failure: a fully matched period produces no drafts.
    try:
        drafts = db.generate_followup_drafts(gstr2b_import_id)
        outcome["drafts_created"] = drafts["created_drafts"]
        outcome["drafts_existing"] = drafts["existing_drafts"]
        outcome["draft_ids"] = list(drafts["draft_ids"])
    except ValueError as exc:
        outcome["drafts_detail"] = str(exc)

    outcome["verifications"] = _reverify_against_batch(gstr2b_import_id)
    return outcome


def _reverify_against_batch(later_import_id: int) -> int:
    """Re-check approved follow-ups against a newly arrived batch.

    Evidence only: ``db.verify_followup_with_later_import`` never changes a
    draft's status or any delivery state. Drafts that are not approved, or
    that came from this very batch, are skipped.
    """
    checked = 0
    for draft in db.get_followup_drafts():
        if draft.get("status") != "APPROVED":
            continue
        if draft.get("gstr2b_import_id") == later_import_id:
            continue
        try:
            outcomes = db.verify_followup_with_later_import(
                int(draft["id"]), int(later_import_id)
            )
        except ValueError:
            # Already verified against this batch, or no linked invoices.
            continue
        checked += len(outcomes)
    return checked


# --- importing one inbound message ----------------------------------------

def _record(entry: Mapping[str, Any], **fields) -> dict:
    """Write the inbound-document audit row and return the caller's result."""
    record = db.record_inbound_document(
        wa_message_id=str(entry.get("id")),
        channel=CHANNEL_WHATSAPP_WEB,
        from_masked=entry.get("fromMasked"),
        kind=str(entry.get("kind") or "document"),
        file_name=(entry.get("fileName") or None),
        mime_type=(entry.get("mimeType") or None),
        received_at=(entry.get("receivedAt") or None),
        message_text=(entry.get("text") or None),
        **fields,
    )
    return record


def _result(record: Mapping[str, Any], workflow: dict | None = None) -> dict:
    result = {
        "inbound_document_id": record["id"],
        "wa_message_id": record["wa_message_id"],
        "status": record["status"],
        "reason": record["reason"],
        "detail": record["detail"],
        "kind": record["kind"],
        "document_kind": record["document_kind"],
        "file_name": record["file_name"],
        "file_hash": record["file_hash"],
        "imported_rows": record["imported_rows"],
        "duplicate_rows": record["duplicate_rows"],
        "rejected_rows": record["rejected_rows"],
        "gstr2b_import_id": record["gstr2b_import_id"],
        "reconciliation_run_id": record["reconciliation_run_id"],
        "drafts_created": record["drafts_created"],
    }
    result["workflow"] = workflow or {
        "ran": False, "reason": None, "periods": [], "run_id": None,
        "drafts_created": 0, "drafts_existing": 0, "draft_ids": [],
        "verifications": 0, "detail": None, "gstr2b_import_id": None,
    }
    return result


def _audit(record: Mapping[str, Any], workflow: dict | None) -> None:
    """Record the import in the system audit log; never raise."""
    try:
        db.record_system_audit_event(
            actor="whatsapp-inbound",
            event_type="whatsapp_inbound_import",
            subject_type="inbound_document",
            subject_id=str(record["id"]),
            details={
                "channel": CHANNEL_WHATSAPP_WEB,
                "from_masked": record.get("from_masked"),
                "file_name": record.get("file_name"),
                "file_hash": record.get("file_hash"),
                "document_kind": record.get("document_kind"),
                "status": record.get("status"),
                "reason": record.get("reason"),
                "imported_rows": record.get("imported_rows"),
                "duplicate_rows": record.get("duplicate_rows"),
                "rejected_rows": record.get("rejected_rows"),
                "gstr2b_import_id": record.get("gstr2b_import_id"),
                "reconciliation_run_id": record.get("reconciliation_run_id"),
                "drafts_created": record.get("drafts_created"),
                "sent_anything": False,
            },
        )
    except Exception:
        # The inbound_documents row is the source of truth; a failed audit
        # write must never change or hide the real outcome.
        pass


def _import_purchase_invoices(content: bytes, file_name: str) -> dict:
    parsed = invoice_import.parse_invoice_file(content, file_name)
    saved = db.save_invoice_import(parsed)
    return {
        "imported_rows": saved["imported"],
        "duplicate_rows": saved["duplicates"],
        "rejected_rows": saved["rejected"],
        "suppliers_created": saved["suppliers_created"],
        "gstr2b_import_id": None,
        "periods": sorted({row.tax_period for row in parsed.rows if row.accepted}),
    }


def _import_gstr2b(content: bytes, file_name: str) -> dict:
    parsed = gstr2b.parse_gstr2b(content, file_name)
    saved = db.save_gstr2b_import(parsed)
    return {
        "imported_rows": saved["accepted_rows"],
        "duplicate_rows": 0,
        "rejected_rows": saved["rejected_rows"],
        "suppliers_created": 0,
        "gstr2b_import_id": saved["import_id"],
        "periods": sorted({
            row.tax_period for row in parsed.rows
            if row.validation_status == "valid" and row.tax_period
        }),
    }


def ingest_inbound_message(
    entry: Mapping[str, Any],
    content: bytes | None,
    *,
    config: InboundConfig | None = None,
    run_workflow: bool = True,
) -> dict:
    """Process one queue entry from the bridge and record what happened.

    `entry` is the metadata the bridge reported; `content` is the decoded
    attachment, or None when the bridge did not (or could not) download it.
    Every path writes exactly one ``inbound_documents`` row, so a rejected or
    unsupported message is as traceable as a successful import.
    """
    cfg = config or load_inbound_config()
    message_id = str(entry.get("id") or "").strip()
    if not message_id:
        raise InboundError("the bridge reported a message with no id")

    # 1. Exact re-delivery of a WhatsApp message: never import twice.
    existing = db.get_inbound_document_by_message_id(message_id)
    if existing is not None:
        return _result({
            **existing,
            "status": STATUS_DUPLICATE,
            "reason": "DUPLICATE_MESSAGE",
            "detail": reason_detail("DUPLICATE_MESSAGE"),
        })

    kind = str(entry.get("kind") or "document")

    # 2. The bridge already decided this one cannot be imported (unsupported
    #    type, too large, download failed). Record its reason verbatim.
    if not entry.get("supported"):
        reason = str(entry.get("reason") or "UNKNOWN_FILE_TYPE")
        status = STATUS_RECORDED if kind == "text" else STATUS_UNSUPPORTED
        if reason in ("FILE_TOO_LARGE", "EMPTY_FILE"):
            status = STATUS_REJECTED
        if reason == "DOWNLOAD_FAILED":
            status = STATUS_FAILED
        record = _record(
            entry,
            status=status,
            reason=reason,
            detail=reason_detail(reason, entry.get("detail")),
            file_size=entry.get("size"),
            file_hash=entry.get("sha256"),
        )
        _audit(record, None)
        return _result(record)

    if not content:
        record = _record(
            entry,
            status=STATUS_FAILED,
            reason="DOWNLOAD_FAILED",
            detail=reason_detail("DOWNLOAD_FAILED"),
            file_size=entry.get("size"),
        )
        _audit(record, None)
        return _result(record)

    # 3. Size cap, enforced here as well as in the bridge: this module is
    #    also reachable from the UI poll endpoint and from tests.
    if len(content) > cfg.max_bytes:
        record = _record(
            entry,
            status=STATUS_REJECTED,
            reason="FILE_TOO_LARGE",
            detail=reason_detail("FILE_TOO_LARGE"),
            file_size=len(content),
            file_hash=sha256_hex(content),
        )
        _audit(record, None)
        return _result(record)

    file_hash = sha256_hex(content)
    file_name = str(entry.get("fileName") or "attachment")

    # 4. The same bytes from a different message: the parsers' natural-key
    #    checks would reject every row anyway, so stop here and say so.
    previous = [
        item for item in db.find_inbound_documents_by_hash(file_hash)
        if item["status"] == STATUS_IMPORTED
    ]
    if previous:
        record = _record(
            entry,
            status=STATUS_DUPLICATE,
            reason="DUPLICATE_FILE",
            detail=(
                reason_detail("DUPLICATE_FILE")
                + f" First imported as inbound document #{previous[0]['id']}."
            ),
            file_size=len(content),
            file_hash=file_hash,
            document_kind=previous[0]["document_kind"],
        )
        _audit(record, None)
        return _result(record)

    # 5. Decide which parser owns the file.
    classification = classify_document(
        content, file_name, mime_type=entry.get("mimeType")
    )
    if not classification.supported:
        record = _record(
            entry,
            status=(
                STATUS_REJECTED
                if classification.reason in ("EMPTY_FILE", "UNREADABLE_FILE",
                                             "UNRECOGNISED_SCHEMA", "AMBIGUOUS_SCHEMA")
                else STATUS_UNSUPPORTED
            ),
            reason=classification.reason,
            detail=classification.detail,
            file_size=len(content),
            file_hash=file_hash,
        )
        _audit(record, None)
        return _result(record)

    stored_path = store_document(content, file_name, config=cfg)

    # 6. Parse and persist. A file-level parser error is a rejection with the
    #    parser's own message, never a crash and never a silent success.
    try:
        if classification.kind == KIND_PURCHASE_INVOICE:
            imported = _import_purchase_invoices(content, file_name)
        else:
            imported = _import_gstr2b(content, file_name)
    except ValueError as exc:
        record = _record(
            entry,
            status=STATUS_REJECTED,
            reason="PARSE_FAILED",
            detail=str(exc),
            file_size=len(content),
            file_hash=file_hash,
            stored_path=str(stored_path),
            document_kind=classification.kind,
        )
        _audit(record, None)
        return _result(record)

    nothing_imported = imported["imported_rows"] == 0
    status = STATUS_REJECTED if nothing_imported else STATUS_IMPORTED
    reason = "NO_VALID_ROWS" if nothing_imported else None
    if nothing_imported and imported["duplicate_rows"]:
        status = STATUS_DUPLICATE
        reason = "DUPLICATE_FILE"

    # 7. The automatic workflow, only for a real import.
    workflow = None
    if status == STATUS_IMPORTED and run_workflow:
        batch_id = imported["gstr2b_import_id"]
        if batch_id is None:
            batch_id = db.find_latest_gstr2b_import_for_periods(imported["periods"])
        workflow = run_auto_workflow(gstr2b_import_id=batch_id)

    record = _record(
        entry,
        status=status,
        reason=reason,
        detail=(reason_detail(reason) if reason else classification.detail),
        file_size=len(content),
        file_hash=file_hash,
        stored_path=str(stored_path),
        document_kind=classification.kind,
        imported_rows=imported["imported_rows"],
        duplicate_rows=imported["duplicate_rows"],
        rejected_rows=imported["rejected_rows"],
        suppliers_created=imported["suppliers_created"],
        gstr2b_import_id=imported["gstr2b_import_id"],
        reconciliation_run_id=(workflow or {}).get("run_id"),
        drafts_created=(workflow or {}).get("drafts_created", 0),
    )
    _audit(record, workflow)
    return _result(record, workflow)


# --- polling the bridge ----------------------------------------------------

class BridgeClient:
    """Thin HTTP client for the bridge's inbound endpoints.

    Lives here rather than in ``webjs.py`` so the outbound provider and the
    inbound reader stay separate: this class has no send method.
    """

    def __init__(self, webjs_config=None) -> None:
        import webjs

        self._webjs = webjs
        self.config = webjs_config or webjs.load_webjs_config()

    def _call(self, method: str, path: str):
        try:
            status, payload = self._webjs._call(self.config, method, path)
        except Exception as exc:
            raise InboundBridgeError(
                f"the local WhatsApp Web bridge could not be reached "
                f"({type(exc).__name__})"
            ) from exc
        return status, payload

    def list_documents(self) -> dict:
        status, payload = self._call("GET", "/inbound")
        if status != 200 or not isinstance(payload, dict):
            raise InboundBridgeError(
                f"the bridge answered /inbound with HTTP {status}"
            )
        documents = payload.get("documents")
        return {
            "documents": documents if isinstance(documents, list) else [],
            "dropped": int(payload.get("dropped") or 0),
            "queued": int(payload.get("queued") or 0),
            "enabled": payload.get("enabled") is not False,
        }

    def fetch_media(self, message_id: str) -> str | None:
        from urllib.parse import quote

        status, payload = self._call(
            "GET", f"/inbound/media?id={quote(str(message_id), safe='')}"
        )
        if status == 404:
            return None
        if status != 200 or not isinstance(payload, dict):
            raise InboundBridgeError(
                f"the bridge answered /inbound/media with HTTP {status}"
            )
        document = payload.get("document")
        if not isinstance(document, dict):
            return None
        data = document.get("data")
        return data if isinstance(data, str) and data else None

    def ack(self, message_id: str) -> bool:
        from urllib.parse import quote

        status, payload = self._call(
            "POST", f"/inbound/ack?id={quote(str(message_id), safe='')}"
        )
        return status == 200 and isinstance(payload, dict) and bool(
            payload.get("acknowledged")
        )


def poll_once(
    *,
    config: InboundConfig | None = None,
    bridge: Any | None = None,
    webjs_config=None,
    limit: int = 20,
) -> dict:
    """Drain the bridge's inbound queue once and report what happened.

    An entry is acknowledged only after its outcome is committed, so a crash
    mid-import leaves the document queued for the next poll rather than
    losing it. A document whose bytes cannot be fetched is left queued too.
    """
    cfg = config or load_inbound_config()
    summary: dict[str, Any] = {
        "enabled": cfg.enabled,
        "processed": 0,
        "imported": 0,
        "duplicates": 0,
        "rejected": 0,
        "unsupported": 0,
        "recorded": 0,
        "failed": 0,
        "dropped": 0,
        "queued": 0,
        "reconciliation_runs": 0,
        "drafts_created": 0,
        "documents": [],
        "problems": cfg.problems(),
    }
    if not cfg.enabled:
        return summary

    client = bridge if bridge is not None else BridgeClient(webjs_config)
    listing = client.list_documents()
    summary["dropped"] = int(listing.get("dropped") or 0)
    summary["queued"] = int(listing.get("queued") or 0)

    run_ids: set[int] = set()
    for entry in list(listing.get("documents") or [])[: max(1, int(limit))]:
        message_id = str(entry.get("id") or "").strip()
        if not message_id:
            continue
        content: bytes | None = None
        if entry.get("supported"):
            try:
                encoded = client.fetch_media(message_id)
                content = base64.b64decode(encoded, validate=True) if encoded else None
            except (InboundBridgeError, ValueError):
                # Leave it queued: the next poll retries the download.
                summary["failed"] += 1
                summary["processed"] += 1
                continue
            if content is None:
                summary["failed"] += 1
                summary["processed"] += 1
                continue

        result = ingest_inbound_message(entry, content, config=cfg)
        summary["processed"] += 1
        summary["documents"].append(result)
        status = result["status"]
        summary[_SUMMARY_KEY.get(status, "failed")] += 1
        workflow = result.get("workflow") or {}
        if workflow.get("run_id"):
            run_ids.add(int(workflow["run_id"]))
        summary["drafts_created"] += int(workflow.get("drafts_created") or 0)
        client.ack(message_id)

    summary["reconciliation_runs"] = len(run_ids)
    return summary


_SUMMARY_KEY = {
    STATUS_IMPORTED: "imported",
    STATUS_DUPLICATE: "duplicates",
    STATUS_REJECTED: "rejected",
    STATUS_UNSUPPORTED: "unsupported",
    STATUS_RECORDED: "recorded",
    STATUS_FAILED: "failed",
}
