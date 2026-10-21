"""MILAAN — ITC gap detection for small Indian traders.

Task 1 foundation: minimal Streamlit shell with SQLite persistence.
Task 2: supplier + invoice demo-data section.
Task 3: deterministic invoice validation (validation.py) with visible
status in the UI.
Task 4: GSTR-2B CSV/XLSX staging import.
Task 5: deterministic reconciliation against one selected GSTR-2B batch.
Task 6: supplier follow-up draft preparation and approval records only.
Task 7: delivery safety and audit history. The UI permits mock-only
simulation; live WhatsApp delivery is disabled in this build.

Run: .venv/bin/streamlit run app.py
"""

import html
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
import db  # noqa: E402
import delivery  # noqa: E402
import gstr2b  # noqa: E402
import security  # noqa: E402
import validation  # noqa: E402

st.set_page_config(
    page_title="MILAAN · ITC operations",
    page_icon=":material/account_balance:",
    layout="wide",
)

APP_CSS = """
<style>
:root {
  --ink: #142621;
  --forest: #0f6455;
  --forest-deep: #0b5044;
  --paper: #f3f6f2;
  --surface: #fcfdfb;
  --line: #d7e2dc;
  --line-strong: #c3d3cc;
  --muted: #5d7069;
  --sidebar: #102a24;
  --sidebar-text: #e8f2ed;
  --sidebar-muted: #9bb6ac;
  --ease-out: cubic-bezier(0.23, 1, 0.32, 1);
  --ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);
  --duration-press: 120ms;
  --duration-ui: 180ms;
}

@keyframes settle-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fade-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

@keyframes expand-in {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

html, body, .stApp, button, input, textarea, select {
  font-family: "Avenir Next", "Segoe UI", system-ui, -apple-system, sans-serif;
}

.stApp {
  background: var(--paper);
  color: var(--ink);
}

[data-testid="stHeader"] {
  background: transparent;
}

.block-container {
  max-width: 1240px;
  padding-top: 1.4rem;
  padding-bottom: 4.5rem;
}

::selection {
  background: #cde8de;
  color: #102a24;
}

* {
  scrollbar-width: thin;
  scrollbar-color: #b4c9c1 transparent;
}

::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}

::-webkit-scrollbar-thumb {
  background: #b4c9c1;
  border: 3px solid var(--paper);
  border-radius: 999px;
}

a {
  color: var(--forest);
  text-underline-offset: 0.18em;
}

h1, h2, h3 {
  color: var(--ink);
  text-wrap: balance;
}

h2 {
  border-top: 1px solid var(--line);
  margin-top: 2.7rem !important;
  padding-top: 1.45rem;
  font-size: 1.48rem !important;
  font-weight: 650 !important;
  letter-spacing: -0.028em;
}

h2 [data-testid="stIconMaterial"] {
  color: var(--forest);
}

h3 {
  letter-spacing: -0.015em;
  font-weight: 650 !important;
}

p, li {
  line-height: 1.62;
}

[data-testid="stCaptionContainer"] p {
  color: var(--muted);
  font-size: 0.86rem;
  line-height: 1.58;
}

hr {
  border-color: var(--line);
  opacity: 0.7;
}

.masthead {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 2rem;
  padding: 1.65rem 1.8rem;
  border: 1px solid #1d3b33;
  border-radius: 1.15rem;
  background: var(--sidebar);
  box-shadow: 0 24px 50px -42px rgba(16, 42, 36, 0.7);
  animation: settle-in 280ms var(--ease-out) both;
}

.masthead-brand {
  display: flex;
  align-items: center;
  gap: 1rem;
  min-width: 0;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 3rem;
  height: 3rem;
  flex: 0 0 auto;
  border: 1px solid rgba(168, 224, 205, 0.2);
  border-radius: 0.9rem;
  background: rgba(255, 255, 255, 0.055);
}

.brand-name {
  color: #f3faf6;
  font-size: 2.15rem;
  font-weight: 700;
  line-height: 0.9;
  letter-spacing: -0.055em;
}

.brand-name span {
  color: #6fd2b2;
}

.brand-sub {
  margin-top: 0.55rem;
  color: #a7c3b9;
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.masthead-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.45rem;
  padding-top: 0.2rem;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.42rem 0.68rem;
  border: 1px solid rgba(205, 236, 225, 0.16);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.055);
  color: #dcebe4;
  font-size: 0.74rem;
  font-weight: 600;
  white-space: nowrap;
}

.status-dot {
  width: 0.42rem;
  height: 0.42rem;
  border-radius: 50%;
  background: #73d9b7;
  box-shadow: 0 0 0 3px rgba(115, 217, 183, 0.12);
}

.status-pill.quiet {
  color: #a8beb6;
}

.status-pill.quiet .status-dot {
  background: #d3a45b;
  box-shadow: 0 0 0 3px rgba(211, 164, 91, 0.12);
}

.status-pill.alert {
  color: #ffd7c9;
}

.status-pill.alert .status-dot {
  background: #e79572;
  box-shadow: 0 0 0 3px rgba(231, 149, 114, 0.13);
}

.masthead-note {
  max-width: 70ch;
  margin: 1.15rem 0 0.2rem;
  color: #536861;
  font-size: 0.95rem;
  line-height: 1.6;
}

section[data-testid="stSidebar"] {
  border-right: 1px solid #203d35;
  background: var(--sidebar);
}

section[data-testid="stSidebar"] > div {
  background: transparent;
}

[data-testid="stSidebarUserContent"] {
  padding-top: 1.5rem;
}

section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] label {
  color: var(--sidebar-text);
}

.rail-label {
  margin: 1.35rem 0 0.55rem;
  color: #80a59a;
  font-size: 0.65rem;
  font-weight: 750;
  letter-spacing: 0.15em;
  text-transform: uppercase;
}

.rail-intro {
  margin: 0 0 0.55rem;
  color: var(--sidebar-muted) !important;
  font-size: 0.77rem;
  line-height: 1.55;
}

.rail-step {
  display: flex;
  gap: 0.65rem;
  align-items: flex-start;
  padding: 0.62rem 0;
  border-bottom: 1px solid rgba(225, 244, 237, 0.075);
  text-decoration: none !important;
}

.rail-step:hover .rail-step-title {
  color: #91e3c7;
}

.rail-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.3rem;
  height: 1.3rem;
  flex: 0 0 auto;
  margin-top: 0.05rem;
  border: 1px solid rgba(145, 207, 187, 0.24);
  border-radius: 0.38rem;
  color: #91c8b6;
  font-size: 0.62rem;
  font-variant-numeric: tabular-nums;
  font-weight: 700;
}

.rail-step-title {
  display: block;
  color: #e8f2ed;
  font-size: 0.8rem;
  font-weight: 600;
  line-height: 1.35;
}

.rail-step-note {
  display: block;
  margin-top: 0.12rem;
  color: #8eaaa0;
  font-size: 0.69rem;
  line-height: 1.4;
}

.rail-system {
  display: flex;
  align-items: center;
  gap: 0.48rem;
  margin: 0.45rem 0;
  color: #c8ddd5 !important;
  font-size: 0.76rem;
}

.rail-path {
  margin: 0.75rem 0 0;
  color: #77958b !important;
  font-size: 0.67rem;
  line-height: 1.55;
  word-break: break-all;
}

section[data-testid="stSidebar"] .stButton > button {
  border-color: rgba(225, 244, 237, 0.2);
  background: transparent;
  color: #e8f2ed;
}

section[data-testid="stSidebar"] .stButton > button:hover {
  border-color: rgba(145, 227, 199, 0.55);
  background: rgba(255, 255, 255, 0.06);
  color: #ffffff;
}

div[data-testid="stMetric"] {
  min-height: 5.15rem;
  padding: 0.9rem 1rem;
  border: 1px solid var(--line);
  border-radius: 0.85rem;
  background: var(--surface);
  box-shadow: 0 12px 24px -24px rgba(20, 38, 33, 0.38);
}

div[data-testid="stMetricLabel"] p {
  color: var(--muted);
  font-size: 0.73rem;
  font-weight: 650;
  letter-spacing: 0.01em;
}

div[data-testid="stMetricValue"] {
  color: var(--ink);
  font-size: 1.55rem;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.035em;
}

div[data-testid="stDataFrame"] {
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 0.85rem;
  background: #ffffff;
}

[data-testid="stExpander"] details {
  border: 1px solid var(--line);
  border-radius: 0.85rem;
  background: var(--surface);
}

[data-testid="stExpander"] summary {
  font-weight: 600;
}

[data-testid="stExpander"] summary:hover {
  color: var(--forest);
}

[data-testid="stAlert"] {
  border-radius: 0.85rem;
  border: 1px solid var(--line);
}

.stButton > button {
  min-height: 2.55rem;
  border-radius: 0.6rem;
  border-color: var(--line-strong);
  color: var(--ink);
  font-weight: 650;
  letter-spacing: -0.005em;
  transition: background-color 160ms ease, border-color 160ms ease,
    color 160ms ease, box-shadow 160ms ease;
}

.stButton > button:hover {
  border-color: #8fb3a6;
  background: #f2f8f5;
  color: var(--forest-deep);
}

.stButton > button[kind="primary"] {
  border-color: var(--forest);
  background: var(--forest);
  color: #ffffff;
  box-shadow: 0 10px 20px -16px rgba(15, 100, 85, 0.7);
}

.stButton > button[kind="primary"]:hover {
  border-color: var(--forest-deep);
  background: var(--forest-deep);
  color: #ffffff;
}

button:focus-visible,
a:focus-visible,
[role="button"]:focus-visible {
  outline: 2px solid #2e9d83;
  outline-offset: 2px;
}

[data-testid="stFileUploaderDropzone"] {
  padding: 1.15rem;
  border: 1px dashed #a8c3b9;
  border-radius: 0.9rem;
  background: #fbfdfb;
}

[data-testid="stFileUploaderDropzone"]:hover {
  border-color: #75a797;
  background: #f6fbf8;
}

[data-baseweb="input"],
[data-baseweb="select"] > div,
[data-baseweb="textarea"] {
  border-radius: 0.6rem !important;
}

button[data-baseweb="tab"] {
  color: var(--muted);
  font-weight: 600;
}

button[data-baseweb="tab"][aria-selected="true"] {
  color: var(--forest);
}

[data-baseweb="tab-highlight"] {
  background: var(--forest);
}

[data-baseweb="tab-border"] {
  background: var(--line);
}

code {
  color: #275f52;
  background: #eaf3ee;
  border-radius: 0.3rem;
}

@media (max-width: 780px) {
  .block-container {
    padding-top: 0.8rem;
    padding-left: 1rem;
    padding-right: 1rem;
  }

  .masthead {
    flex-direction: column;
    gap: 1.15rem;
    padding: 1.3rem;
    border-radius: 0.95rem;
  }

  .masthead-meta {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
  }

  .brand-name {
    font-size: 1.85rem;
  }

  h2 {
    margin-top: 2.15rem !important;
    font-size: 1.32rem !important;
  }
}
</style>
"""


def _inject_styles() -> None:
    st.markdown(APP_CSS, unsafe_allow_html=True)


def _render_masthead(ready: bool, actor: str) -> None:
    status_class = "status-pill" if ready else "status-pill alert"
    status_text = "Database ready" if ready else "Database needs attention"
    actor_label = (
        "Local demo" if actor == "local-demo"
        else html.escape(actor)
    )
    st.markdown(
        f"""
        <div class="masthead">
          <div class="masthead-brand">
            <div class="brand-mark" aria-hidden="true">
              <svg width="26" height="26" viewBox="0 0 26 26" fill="none">
                <path d="M5 8.25h8.25M5 17.75h8.25"
                      stroke="#7dd8ba" stroke-width="2.1"
                      stroke-linecap="round"/>
                <path d="M15.25 6.5 21 13l-5.75 6.5"
                      stroke="#f0faf5" stroke-width="2.1"
                      stroke-linecap="round"
                      stroke-linejoin="round"/>
              </svg>
            </div>
            <div>
              <div class="brand-name">MILAAN<span>.</span></div>
              <div class="brand-sub">GST reconciliation workspace</div>
            </div>
          </div>
          <div class="masthead-meta">
            <span class="{status_class}">
              <span class="status-dot"></span>{status_text}
            </span>
            <span class="status-pill quiet">
              <span class="status-dot"></span>Live sending off
            </span>
            <span class="status-pill quiet">{actor_label}</span>
          </div>
        </div>
        <p class="masthead-note">
          Review purchase records, reconcile normalized GSTR-2B data, and
          keep every follow-up decision traceable. Findings support human
          review and do not determine ITC eligibility.
        </p>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar(ready: bool, actor: str) -> None:
    database_path = html.escape(str(db.get_database_path().resolve()))
    status_text = "Database ready" if ready else "Database needs attention"
    with st.sidebar:
        st.markdown(
            """
            <div class="rail-label">Reconciliation flow</div>
            <p class="rail-intro">Work from validation to evidence. Every
            stage is persisted, and approval never sends a message.</p>
            <a class="rail-step" href="#validation">
              <span class="rail-num">1</span>
              <span><span class="rail-step-title">Invoice validation</span>
              <span class="rail-step-note">Field, tax and duplicate checks</span></span>
            </a>
            <a class="rail-step" href="#import-gstr2b">
              <span class="rail-num">2</span>
              <span><span class="rail-step-title">GSTR-2B intake</span>
              <span class="rail-step-note">Normalized CSV or XLSX with audit trail</span></span>
            </a>
            <a class="rail-step" href="#reconciliation">
              <span class="rail-num">3</span>
              <span><span class="rail-step-title">Reconciliation</span>
              <span class="rail-step-note">Exact evidence and review routing</span></span>
            </a>
            <a class="rail-step" href="#follow-ups">
              <span class="rail-num">4</span>
              <span><span class="rail-step-title">Follow-up drafts</span>
              <span class="rail-step-note">Human approval before delivery review</span></span>
            </a>
            <a class="rail-step" href="#later-statement">
              <span class="rail-num">5</span>
              <span><span class="rail-step-title">Later-statement check</span>
              <span class="rail-step-note">Append evidence without changing history</span></span>
            </a>
            <a class="rail-step" href="#delivery">
              <span class="rail-num">6</span>
              <span><span class="rail-step-title">Delivery audit</span>
              <span class="rail-step-note">Simulation and operator evidence</span></span>
            </a>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            f"""
            <div class="rail-label">Environment</div>
            <div class="rail-system">
              <span class="status-dot"></span>{html.escape(status_text)}
            </div>
            <div class="rail-system">
              <span class="status-dot"></span>Live messages disabled
            </div>
            <p class="rail-path">{database_path}</p>
            """,
            unsafe_allow_html=True,
        )
        with st.expander("Operational audit"):
            try:
                audit_events = db.get_system_audit_events()
            except Exception as exc:
                st.error(f"Could not load audit history: {exc}")
            else:
                if audit_events:
                    st.dataframe(
                        audit_events,
                        alt="Recent operational audit events",
                        hide_index=True,
                        height=220,
                    )
                else:
                    st.caption("No operational actions recorded yet.")
        if actor != "local-demo":
            st.caption(f"Signed in as {actor}")
            if st.button("Sign out", key="sign_out"):
                st.logout()


def _require_authorized_user() -> str:
    """Gate production access with configured OIDC, leaving local demos open."""
    try:
        config = security.load_security_config()
    except ValueError as exc:
        st.error(f"Security configuration error: {exc}")
        st.stop()
    if not config.auth_required:
        return "local-demo"
    try:
        is_logged_in = bool(st.user.is_logged_in)
    except AttributeError:
        st.title("MILAAN sign-in required")
        st.error(
            "OIDC is required but has not been configured. Add valid provider "
            "settings to .streamlit/secrets.toml before serving this workspace."
        )
        st.stop()
    if not is_logged_in:
        st.title("MILAAN sign-in required")
        st.info(
            "This production workspace requires an approved OIDC identity. "
            "Configure .streamlit/secrets.toml from secrets.example.toml "
            "before signing in."
        )
        if st.button("Sign in", key="sign_in"):
            st.login()
        st.stop()
    email = getattr(st.user, "email", None)
    if not security.email_is_allowed(email, config.allowed_email_domains):
        st.error("Your signed-in email is not authorized for this workspace.")
        if st.button("Sign out", key="unauthorized_sign_out"):
            st.logout()
        st.stop()
    return str(email).strip().lower()


def _audit(actor: str, event: str, subject: str, subject_id: object, **details: object) -> None:
    """Record operational actions without disrupting a completed workflow."""
    try:
        db.record_system_audit_event(
            actor=actor,
            event_type=event,
            subject_type=subject,
            subject_id=str(subject_id),
            details=details,
        )
    except Exception as exc:
        st.warning(f"Action completed, but its operational audit entry failed: {exc}")


def main() -> None:
    _inject_styles()
    actor = _require_authorized_user()

    try:
        db.init_db()
        ready = db.is_db_ready()
    except Exception as exc:  # surface real errors, never hide them
        st.error(f"Database error: {exc}")
        st.stop()

    _render_masthead(ready, actor)
    _render_sidebar(ready, actor)

    st.header("Demo data", icon=":material/science:", anchor="demo")
    st.caption(
        "Seed a synthetic workspace for evaluation. Seeding is idempotent "
        "and never changes existing records; every supplier and amount is "
        "fabricated."
    )
    seed_error = None
    seed_result = None
    if st.button("Seed demo data", key="seed_demo"):
        try:
            seed_result = db.seed_demo_data()
            _audit(actor, "demo_seeded", "database", db.get_database_path())
        except Exception as exc:  # surface the real error to the user
            seed_error = exc

    if seed_error is not None:
        st.error(f"Seed failed: {seed_error}")
    elif seed_result is not None:
        st.success(
            f"Seed complete: {seed_result['suppliers']} supplier(s) added, "
            f"{seed_result['invoices']} invoice(s) added. "
            "Existing records were not modified."
        )

    st.header(
        "Invoice validation",
        icon=":material/fact_check:",
        anchor="validation",
    )
    st.caption(
        "Check GSTIN structure, invoice arithmetic, dates, and duplicate "
        "keys. A pass is a field check, not a live registration or ITC "
        "decision."
    )
    with st.expander("Validation rules and limits"):
        st.caption(
            "Checks GSTIN format + check digit, required fields, tax "
            "arithmetic (integer paise), intra- vs inter-state structure and "
            "duplicates. GSTIN checks are format/checksum only — live GST "
            "registration is NOT verified. A 'valid' result means these "
            "field checks passed; it does not determine ITC eligibility. "
            "Invalid and needs-review invoices are excluded from "
            "reconciliation."
        )
    validate_error = None
    validate_results = None
    if st.button(
        "Validate all invoices",
        key="validate_all",
        type="primary",
    ):
        try:
            validate_results = db.revalidate_all_invoices()
            _audit(actor, "invoices_validated", "invoice_set", "all", count=len(validate_results))
        except Exception as exc:  # surface the real error, never swallow it
            validate_error = exc

    if validate_error is not None:
        st.error(f"Validation failed: {validate_error}")
    elif validate_results is not None:
        if not validate_results:
            st.info("No invoices to validate yet — seed or add invoices first.")
        else:
            counts = {status: 0 for status in validation.STATUSES}
            for entry in validate_results:
                counts[entry["result"].status] += 1
            st.success(
                f"Validated {len(validate_results)} invoice(s): "
                f"{counts['valid']} valid, "
                f"{counts['needs_review']} need review, "
                f"{counts['invalid']} invalid."
            )
            with st.expander("Validation details", expanded=True):
                for entry in validate_results:
                    result = entry["result"]
                    if not result.errors and not result.warnings:
                        continue
                    st.markdown(
                        f"**{entry['invoice_number']}** — status "
                        f"`{result.status}`"
                    )
                    for issue in result.errors:
                        st.write(
                            f"- ERROR `{issue.code}` ({issue.field}): "
                            f"{issue.message}"
                        )
                    for issue in result.warnings:
                        st.write(
                            f"- warning `{issue.code}` ({issue.field}): "
                            f"{issue.message}"
                        )

    try:
        suppliers = db.get_suppliers()
        invoices = db.get_invoices()
        totals = db.get_totals()
        status_summary = db.get_validation_summary()
    except Exception as exc:
        st.error(f"Database error: {exc}")
        st.stop()

    metric_cols = st.columns(2)
    metric_cols[0].metric(
        "Invoices", totals["invoice_count"], border=True
    )
    metric_cols[1].metric(
        "Invoice GST recorded",
        f"₹{db.format_paise(totals['total_gst_paise'])}",
        border=True,
    )

    status_cols = st.columns(4)
    status_cols[0].metric("Valid", status_summary["valid"], border=True)
    status_cols[1].metric(
        "Needs review", status_summary["needs_review"], border=True
    )
    status_cols[2].metric("Invalid", status_summary["invalid"], border=True)
    status_cols[3].metric(
        "Not yet validated", status_summary["pending"], border=True
    )

    st.markdown("**Suppliers**")
    if suppliers:
        st.dataframe(
            suppliers,
            alt="Suppliers registered in this workspace",
            hide_index=True,
        )
    else:
        st.info("No suppliers yet — click 'Seed demo data' to create synthetic ones.")

    st.markdown("**Invoices by validation status**")
    if invoices:
        groups = [
            ("All", invoices),
            ("Valid", [i for i in invoices if i["Validation"] == "valid"]),
            ("Needs review", [i for i in invoices if i["Validation"] == "needs_review"]),
            ("Invalid", [i for i in invoices if i["Validation"] == "invalid"]),
            ("Pending", [i for i in invoices
                         if i["Validation"] in ("pending", "mismatch")]),
        ]
        tabs = st.tabs([f"{name} ({len(rows)})" for name, rows in groups])
        for tab, (name, rows) in zip(tabs, groups):
            with tab:
                if rows:
                    st.dataframe(
                        rows,
                        alt=f"Invoices in the {name.lower()} validation group",
                        hide_index=True,
                    )
                else:
                    st.info(f"No {name.lower()} invoices.")
    else:
        st.info("No invoices yet — click 'Seed demo data' to create synthetic ones.")

    st.markdown("**Invoices ready for reconciliation checks**")
    passed_validation = [
        invoice
        for invoice in invoices
        if validation.can_message_supplier(invoice["Validation"])
    ]
    if invoices:
        st.write(
            f"{len(passed_validation)} of {len(invoices)} invoice(s) passed "
            "validation and can proceed to reconciliation. Follow-up draft "
            "eligibility also depends on the selected batch and its latest "
            "reconciliation evidence."
        )
    else:
        st.info("No invoices yet.")

    st.header(
        "GSTR-2B import",
        icon=":material/upload_file:",
        anchor="import-gstr2b",
    )
    st.caption(
        "Stage normalized CSV or XLSX rows with a full audit trail. Import "
        "never modifies purchase invoices and performs no reconciliation."
    )
    with st.expander("Accepted schema and checks"):
        st.caption(
            "Required columns: supplier_gstin, invoice_number, invoice_date "
            "(YYYY-MM-DD), tax_period (YYYY-MM), taxable_value, cgst, sgst, "
            "igst, total_gst. Amounts use rupee decimals and are stored as "
            "integer paise. GSTIN checks are syntax/checksum only; live GST "
            "registration is NOT verified. XLSX reads the active worksheet."
        )

    uploaded = st.file_uploader(
        "Upload GSTR-2B file",
        type=["csv", "xlsx"],
        key="gstr2b_upload",
    )
    if uploaded is not None:
        content = uploaded.getvalue()
        file_hash = gstr2b.sha256_hex(content)
        prior_imports = None
        try:
            prior_imports = db.find_gstr2b_imports_by_hash(file_hash)
        except Exception as exc:
            st.error(f"Could not check previous uploads: {exc}")

        if prior_imports:
            batches = ", ".join(
                f"#{row['id']} ({row['imported_at']}, "
                f"{row['accepted_rows']}/{row['total_rows']} accepted)"
                for row in prior_imports
            )
            st.warning(
                f"This exact file (SHA-256 {file_hash[:12]}…) was already "
                f"imported as batch {batches}. Nothing is imported "
                "automatically. Importing again creates a separate batch; "
                "it is never silently deduplicated."
            )
            confirm_reimport = st.checkbox(
                "Import this identical file again as a new batch",
                key="gstr2b_reimport_confirm",
            )
        else:
            confirm_reimport = True
            st.caption(f"SHA-256: {file_hash}")

        if st.button(
            "Import GSTR-2B file",
            key="gstr2b_import",
            disabled=not confirm_reimport,
            type="primary",
        ):
            try:
                parsed = gstr2b.parse_gstr2b(content, uploaded.name)
                saved = db.save_gstr2b_import(parsed)
                _audit(
                    actor, "gstr2b_imported", "gstr2b_import", saved["import_id"],
                    file_name=saved["file_name"], accepted_rows=saved["accepted_rows"],
                    rejected_rows=saved["rejected_rows"],
                )
            except Exception as exc:  # surface the real error, never hide it
                st.error(f"GSTR-2B import failed: {exc}")
            else:
                st.success(
                    f"Batch #{saved['import_id']} imported: "
                    f"{saved['accepted_rows']} of {saved['total_rows']} "
                    f"row(s) accepted, {saved['rejected_rows']} rejected. "
                    "All source rows are kept in the audit trail."
                )
                if saved["rejected_rows"]:
                    st.warning(
                        "Some rows were rejected; inspect the row-level "
                        "issues in the batch below."
                    )

    try:
        imports = db.get_gstr2b_imports()
    except Exception as exc:
        st.error(f"Database error: {exc}")
        st.stop()

    if imports:
        st.markdown("**Import batches**")
        st.dataframe(
            imports,
            alt="Imported normalized GSTR-2B batches",
            hide_index=True,
        )

        selected_batch = st.selectbox(
            "Inspect batch",
            options=[row["Batch"] for row in imports],
            key="gstr2b_batch",
        )
        batch_rows = db.get_gstr2b_rows(selected_batch)
        if batch_rows:
            st.dataframe(
                batch_rows,
                alt="Rows and validation outcomes in the selected import batch",
                hide_index=True,
            )
            rejected_rows = [
                row for row in batch_rows if row["Status"] != "valid"
            ]
            if rejected_rows:
                with st.expander("Row-level issues", expanded=True):
                    for row in rejected_rows:
                        st.markdown(
                            f"**Row {row['Row']}** — status `{row['Status']}`"
                        )
                        st.write(row["Issues"])
        else:
            st.info("This batch has no rows.")
    else:
        st.info("No GSTR-2B imports yet.")

    st.header(
        "Reconciliation",
        icon=":material/compare_arrows:",
        anchor="reconciliation",
    )
    st.caption(
        "Match on GSTIN, invoice number, tax period, and exact GST amounts. "
        "Ambiguity goes to review; no candidate is auto-selected."
    )
    with st.expander("Matching policy and limits"):
        st.caption(
            "Exact-match-first on normalized supplier GSTIN + invoice number "
            "+ tax period; GST amounts are compared in integer paise. "
            "Tolerance 0 means exact equality. Non-exact candidates are "
            "flagged for review, never auto-accepted. Duplicate or ambiguous "
            "matches are NEEDS_REVIEW and no candidate is chosen. 'Missing "
            "from 2B' only means this batch has no usable counterpart — it "
            "does NOT prove supplier non-filing, ITC eligibility or "
            "recoverability. No AI is used."
        )

    if not imports:
        st.info(
            "Import a GSTR-2B batch first. Reconciliation requires a "
            "selected batch and at least one validated purchase invoice."
        )
    else:
        batch_labels = {
            f"Batch #{row['Batch']} — {row['File']} "
            f"({row['Accepted']}/{row['Rows']} accepted)": row["Batch"]
            for row in imports
        }
        batch_column, tolerance_column = st.columns([2.4, 1])
        selected_label = batch_column.selectbox(
            "GSTR-2B batch to reconcile",
            options=list(batch_labels),
            key="recon_batch",
        )
        tolerance = tolerance_column.number_input(
            "Amount tolerance (paise; 0 = exact)",
            min_value=0,
            value=0,
            step=1,
            key="recon_tolerance",
        )
        if st.button(
            "Reconcile selected batch",
            key="reconcile",
            type="primary",
        ):
            try:
                summary = db.reconcile_gstr2b_import(
                    batch_labels[selected_label], int(tolerance)
                )
                _audit(
                    actor, "reconciliation_completed", "reconciliation_run",
                    summary["run_id"], batch_id=summary["gstr2b_import_id"],
                    tolerance_paise=summary["amount_tolerance_paise"],
                )
            except ValueError as exc:
                st.warning(str(exc))
            except Exception as exc:  # surface the real error
                st.error(f"Reconciliation failed: {exc}")
            else:
                message = (
                    f"Run #{summary['run_id']} completed for batch "
                    f"#{summary['gstr2b_import_id']}: "
                    f"{summary['matched_count']} matched, "
                    f"{summary['amount_mismatch_count']} amount mismatch, "
                    f"{summary['missing_count']} missing from 2B, "
                    f"{summary['needs_review_count']} needs review, "
                    f"{summary['not_eligible_count']} not eligible."
                )
                if summary["matched_count"] == 0:
                    st.warning(
                        message + " No invoices were matched in this run."
                    )
                else:
                    st.success(message)

    try:
        runs = db.get_reconciliation_runs()
    except Exception as exc:
        st.error(f"Database error: {exc}")
    else:
        if runs:
            st.markdown("**Reconciliation run history** (newest first)")
            st.dataframe(
                runs,
                alt="Persisted reconciliation runs",
                hide_index=True,
            )
            selected_run = st.selectbox(
                "Inspect reconciliation run",
                options=[row["Run"] for row in runs],
                key="recon_run",
            )
            run_summary = db.get_reconciliation_summary(selected_run)
            if run_summary is not None:
                by_status = run_summary["by_status"]
                columns = st.columns(4)
                for column, status, label in zip(
                    columns,
                    (
                        "MATCHED",
                        "AMOUNT_MISMATCH",
                        "MISSING_FROM_2B",
                        "NEEDS_REVIEW",
                    ),
                    (
                        "Matched",
                        "Amount mismatch",
                        "Missing from 2B",
                        "Needs review",
                    ),
                ):
                    item = by_status[status]
                    column.metric(
                        label,
                        f"{item['count']} · Rs "
                        f"{db.format_paise(item['total_gst_paise'])}",
                    )

                run = run_summary["run"]
                st.caption(
                    f"Selected run #{run['id']} (batch "
                    f"#{run['gstr2b_import_id']}, {run['file_name']}), "
                    f"tolerance {run['amount_tolerance_paise']} paise, "
                    f"created {run['created_at']}. "
                    f"Not eligible: {run['not_eligible_count']} invoice(s). "
                    f"Rupee totals are invoice GST per status. A missing "
                    "row is not evidence of recoverable ITC."
                )
                if by_status["MATCHED"]["count"] == 0:
                    st.warning(
                        "This run has zero matched invoices — inspect the "
                        "evidence table and reason codes below."
                    )
                st.dataframe(
                    db.get_reconciliation_results(selected_run),
                    alt="Invoice-level reconciliation statuses and evidence",
                    hide_index=True,
                )
        else:
            st.info("No reconciliation runs yet.")

    st.header(
        "Supplier follow-ups",
        icon=":material/draft:",
        anchor="follow-ups",
    )
    st.caption(
        "Draft follow-ups only for validated invoices with an unambiguous "
        "MISSING_FROM_2B result. Human approval records the decision; live "
        "delivery remains disabled."
    )
    with st.expander("Who is eligible and what approval means"):
        st.caption(
            "Only validated purchase invoices with an unambiguous "
            "MISSING_FROM_2B result in the latest reconciliation run for the "
            "selected batch can become drafts. Matched, amount-mismatched, "
            "needs-review, not-eligible and invalid invoices are excluded. "
            "Drafts require human review and explicit approval. Approval "
            "records that review; live delivery is disabled. The optional "
            "mock action below never uses the network. A missing 2B entry "
            "does not establish supplier fault, ITC eligibility or "
            "recoverability. No contact details are invented."
        )

    flash = st.session_state.pop("followup_flash", None)
    if flash is not None:
        level, message = flash
        if level == "success":
            st.success(message)
        elif level == "warning":
            st.warning(message)
        else:
            st.info(message)

    if not imports:
        st.info(
            "Import a GSTR-2B batch and run reconciliation first; draft "
            "preparation needs reconciliation evidence."
        )
    else:
        draft_batch_labels = {
            f"Batch #{row['Batch']} — {row['File']} "
            f"({row['Accepted']}/{row['Rows']} accepted)": row["Batch"]
            for row in imports
        }
        draft_batch_label = st.selectbox(
            "GSTR-2B batch for follow-up preparation",
            options=list(draft_batch_labels),
            key="followup_batch",
        )
        draft_batch_id = draft_batch_labels[draft_batch_label]
        try:
            eligibility = db.get_followup_eligibility(draft_batch_id)
        except Exception as exc:
            st.error(f"Could not evaluate follow-up eligibility: {exc}")
        else:
            followup_run = eligibility["run"]
            if followup_run is None:
                st.info(
                    f"No reconciliation run exists for batch "
                    f"#{draft_batch_id} yet. Reconcile this batch first."
                )
            else:
                st.caption(
                    f"Using the latest reconciliation run "
                    f"#{followup_run['id']} for batch #{draft_batch_id} "
                    f"({followup_run['file_name']})."
                )
                plans = eligibility["plans"]
                excluded = eligibility["excluded"]

                if plans:
                    summary_rows = []
                    for plan in plans:
                        summary_rows.append({
                            "Supplier": plan["supplier_name"],
                            "GSTIN": plan["supplier_gstin"],
                            "Invoices": len(plan["invoices"]),
                            "Taxable (Rs)": db.format_paise(
                                plan["total_taxable_value_paise"]
                            ),
                            "GST (Rs)": db.format_paise(
                                plan["total_gst_paise"]
                            ),
                            "Phone on file": (
                                plan["contact_phone"] or "not on file"
                            ),
                            "Channel": (
                                "available"
                                if plan["channel_available"]
                                else "unavailable"
                            ),
                            "Planned status": plan["status"],
                            "Review reasons": ", ".join(
                                plan["review_reasons"]
                            ),
                        })
                    st.markdown("**Eligible supplier groups (preview)**")
                    st.dataframe(
                        summary_rows,
                        alt="Supplier groups eligible for follow-up review",
                        hide_index=True,
                    )

                    with st.expander("Preview planned draft text"):
                        for plan in plans:
                            st.markdown(
                                f"**{plan['supplier_name']}** "
                                f"(`{plan['supplier_gstin'] or 'no GSTIN'}`)"
                            )
                            st.text(plan["draft_text"])

                    if st.button(
                        "Generate follow-up drafts",
                        key="generate_followups",
                        type="primary",
                    ):
                        try:
                            generated = db.generate_followup_drafts(
                                draft_batch_id
                            )
                            _audit(
                                actor, "followup_drafts_generated", "gstr2b_import",
                                draft_batch_id, created=generated["created_drafts"],
                                existing=generated["existing_drafts"],
                            )
                        except ValueError as exc:
                            st.warning(str(exc))
                        except Exception as exc:
                            st.error(f"Draft generation failed: {exc}")
                        else:
                            st.session_state["followup_flash"] = (
                                "success",
                                f"{generated['created_drafts']} draft(s) "
                                f"created, {generated['existing_drafts']} "
                                "already existed for this run (existing "
                                "drafts and edits were left untouched). No "
                                "message was sent.",
                            )
                            st.rerun()
                else:
                    st.warning(
                        "No invoices in this run are eligible for supplier "
                        "follow-up drafts. Nothing was generated."
                    )

                if excluded:
                    with st.expander("Ineligible invoices and why"):
                        st.dataframe([
                            {
                                "Invoice #": item["invoice_id"],
                                "Invoice number": item["invoice_number"],
                                "Supplier": item["supplier_name"],
                                "Invoice validation": item["validation_status"],
                                "Reconciliation": item["match_status"],
                                "Excluded because": item["reason"],
                            }
                            for item in excluded
                        ], alt="Invoices excluded from follow-up and reasons",
                            hide_index=True)
                else:
                    st.caption("No invoices were excluded in this run.")

                drafts = db.get_followup_drafts(
                    gstr2b_import_id=draft_batch_id
                )
                if drafts:
                    st.markdown("**Saved drafts for this batch**")
                    st.dataframe(
                        drafts,
                        alt="Saved follow-up drafts and status",
                        hide_index=True,
                    )
                    draft_labels = {
                        f"#{row['Draft']} — {row['Supplier']} "
                        f"(run #{row['Run']}, {row['Status']})": row["Draft"]
                        for row in drafts
                    }
                    selected_draft_label = st.selectbox(
                        "Inspect draft",
                        options=list(draft_labels),
                        key="followup_draft",
                    )
                    draft_id = draft_labels[selected_draft_label]
                    draft = db.get_followup_draft(draft_id)
                    if draft is not None:
                        editable = draft["status"] in (
                            "DRAFT",
                            "NEEDS_REVIEW",
                        )
                        st.markdown(
                            f"**Draft #{draft['id']} — "
                            f"{draft['supplier_name']}**  \n"
                            f"Status: `{draft['status']}` · Language: "
                            f"`{draft['language']}` · Phone on file: "
                            f"{draft['contact_phone'] or 'none'} · "
                            f"Delivery channel: "
                            f"{'available' if draft['channel_available'] else 'unavailable'}"
                        )
                        st.warning(
                            "No message has been sent yet. Approval makes this draft "
                            "eligible for the delivery section below, where "
                            "explicit confirmation is still required."
                        )
                        if draft["review_reasons"]:
                            st.caption(
                                "Review required: "
                                + ", ".join(draft["review_reasons"])
                            )

                        edited_text = st.text_area(
                            "Draft text",
                            value=draft["draft_text"],
                            height=260,
                            key=f"followup_text_{draft_id}",
                            disabled=not editable,
                        )
                        save_column, approve_column, skip_column = (
                            st.columns(3)
                        )
                        if save_column.button(
                            "Save edits",
                            key=f"followup_save_{draft_id}",
                            disabled=not editable,
                        ):
                            try:
                                db.update_followup_draft_text(
                                    draft_id, edited_text
                                )
                                _audit(actor, "followup_draft_edited", "followup_draft", draft_id)
                            except (ValueError, KeyError) as exc:
                                st.warning(str(exc))
                            except Exception as exc:
                                st.error(f"Could not save draft: {exc}")
                            else:
                                st.session_state["followup_flash"] = (
                                    "success",
                                    f"Draft #{draft_id} edits saved. No "
                                    "message was sent.",
                                )
                                st.rerun()

                        problems = db.get_followup_approval_problems(draft_id)
                        can_approve = (
                            draft["status"] == "DRAFT" and not problems
                        )
                        confirmed = approve_column.checkbox(
                            "Confirm approval (no send occurs)",
                            key=f"followup_confirm_{draft_id}",
                            disabled=not can_approve,
                        )
                        if approve_column.button(
                            "Approve for delivery review",
                            key=f"followup_approve_{draft_id}",
                            disabled=not can_approve or not confirmed,
                            type="primary",
                        ):
                            try:
                                db.approve_followup_draft(
                                    draft_id, confirmed=confirmed
                                )
                                _audit(actor, "followup_draft_approved", "followup_draft", draft_id)
                            except (ValueError, KeyError) as exc:
                                st.warning(str(exc))
                            except Exception as exc:
                                st.error(f"Approval failed: {exc}")
                            else:
                                st.session_state["followup_flash"] = (
                                    "success",
                                    f"Draft #{draft_id} APPROVED for "
                                    "delivery review. Human review is "
                                    "recorded. No message has been sent. "
                                    "Live delivery is disabled. The "
                                    "delivery section below is mock-only.",
                                )
                                st.rerun()

                        if skip_column.button(
                            "Skip draft",
                            key=f"followup_skip_{draft_id}",
                            disabled=draft["status"] == "SKIPPED",
                        ):
                            try:
                                db.skip_followup_draft(draft_id)
                                _audit(actor, "followup_draft_skipped", "followup_draft", draft_id)
                            except (ValueError, KeyError) as exc:
                                st.warning(str(exc))
                            except Exception as exc:
                                st.error(f"Could not skip draft: {exc}")
                            else:
                                st.session_state["followup_flash"] = (
                                    "info",
                                    f"Draft #{draft_id} marked SKIPPED. No "
                                    "message was sent.",
                                )
                                st.rerun()

                        if draft["status"] in ("DRAFT", "NEEDS_REVIEW"):
                            if problems:
                                st.caption(
                                    "Approval disabled: "
                                    + ", ".join(problems)
                                )
                            elif not draft["channel_available"]:
                                st.caption(
                                    "Approval disabled: no delivery channel "
                                    "(no phone number on file)."
                                )

                        st.markdown("**Supporting invoices**")
                        st.dataframe(
                            db.get_followup_draft_invoices(draft_id),
                            alt="Invoices supporting the selected follow-up draft",
                            hide_index=True,
                        )
                        st.caption(
                            f"Total taxable value: Rs "
                            f"{db.format_paise(draft['total_taxable_value_paise'])}"
                            f" · Total GST: Rs "
                            f"{db.format_paise(draft['total_gst_paise'])}. "
                            f"Inclusion reason: {draft['inclusion_reason']}."
                        )
                        with st.expander("Draft status history"):
                            st.dataframe(
                                db.get_followup_draft_events(draft_id),
                                alt="Audit history for the selected follow-up draft",
                                hide_index=True,
                            )
                else:
                    st.info(
                        "No follow-up drafts saved yet for this batch. "
                        "Preview the eligible groups above, then generate "
                        "drafts."
                    )

    st.header(
        "Later-statement verification",
        icon=":material/fact_check:",
        anchor="later-statement",
    )
    st.caption(
        "Compare an approved follow-up with a separate imported batch. "
        "Evidence is appended; invoices, prior runs, and draft status stay "
        "unchanged."
    )
    with st.expander("What the evidence means"):
        st.caption(
            "This records whether a unique supplier/invoice row appears and "
            "whether its GST amount agrees. A missing row does not prove "
            "supplier non-filing or establish ITC eligibility or recovery. "
            "Invoices and prior reconciliation evidence remain unchanged."
        )
    try:
        verification_drafts = [
            row for row in db.get_followup_drafts()
            if row["Status"] == "APPROVED"
        ]
        verification_imports = db.get_gstr2b_imports()
    except Exception as exc:
        st.error(f"Could not load verification data: {exc}")
        verification_drafts = []
        verification_imports = []

    if not verification_drafts:
        st.info("Approve a follow-up draft before checking a later statement.")
    elif len(verification_imports) < 2:
        st.info(
            "Import the later GSTR-2B batch first. The original follow-up "
            "batch cannot be selected as later evidence."
        )
    else:
        verify_draft_labels = {
            f"Draft #{row['Draft']} — {row['Supplier']} (original batch #{row['Batch']})": row
            for row in verification_drafts
        }
        verify_draft_label = st.selectbox(
            "Approved follow-up to check",
            options=list(verify_draft_labels),
            key="later_verify_draft",
        )
        verify_draft = verify_draft_labels[verify_draft_label]
        later_import_options = [
            row for row in verification_imports
            if row["Batch"] != verify_draft["Batch"]
        ]
        if not later_import_options:
            st.info("Import a separate batch for later-statement evidence.")
        else:
            later_import_labels = {
                f"Batch #{row['Batch']} — {row['File']} ({row['Accepted']}/{row['Rows']} accepted)": row["Batch"]
                for row in later_import_options
            }
            later_import_label = st.selectbox(
                "Later GSTR-2B batch",
                options=list(later_import_labels),
                key="later_verify_import",
            )
            if st.button(
                "Record later-statement check",
                key="later_verify_run",
                type="primary",
            ):
                try:
                    db.verify_followup_with_later_import(
                        verify_draft["Draft"],
                        later_import_labels[later_import_label],
                    )
                    _audit(
                        actor, "later_statement_checked", "followup_draft",
                        verify_draft["Draft"],
                        later_import_id=later_import_labels[later_import_label],
                    )
                except (KeyError, ValueError) as exc:
                    st.warning(str(exc))
                except Exception as exc:
                    st.error(f"Later-statement check failed: {exc}")
                else:
                    st.success(
                        "Evidence check recorded. Source invoices, prior "
                        "reconciliation results, and follow-up status were not changed."
                    )

    try:
        verification_history = db.get_followup_verifications()
    except Exception as exc:
        st.error(f"Could not load later-statement history: {exc}")
        verification_history = []
    if verification_history:
        st.dataframe([
            {
                "Draft": row["followup_draft_id"],
                "Invoice #": row["invoice_id"],
                "Later batch": row["later_import_id"],
                "File": row["later_file_name"],
                "Outcome": row["outcome"],
                "Amount difference (Rs)": (
                    "" if row["amount_difference_paise"] is None
                    else db.format_paise(row["amount_difference_paise"])
                ),
                "Evidence": row["reason"],
                "Recorded": row["created_at"],
            }
            for row in verification_history
        ], alt="Audited later-statement checks for approved follow-ups",
            hide_index=True)
    else:
        st.info("No later-statement checks recorded yet.")

    st.header(
        "Delivery status",
        icon=":material/history:",
        anchor="delivery",
    )
    st.caption(
        "Inspect delivery evidence or run a simulated send. Live WhatsApp "
        "delivery is disabled and approval alone sends nothing."
    )
    with st.expander("Delivery safety and consent"):
        st.caption(
            "Live WhatsApp delivery is disabled in this build. A draft "
            "approval only records human review; it never sends a message. "
            "Mock delivery is available for workflow testing and makes no "
            "network request. A phone number on file does not prove consent "
            "or ownership."
        )

    delivery_flash = st.session_state.pop("delivery_flash", None)
    if delivery_flash is not None:
        level, message = delivery_flash
        if level == "success":
            st.success(message)
        elif level == "warning":
            st.warning(message)
        else:
            st.info(message)

    mode = st.radio(
        "Delivery mode",
        options=["Simulated (mock)", "WhatsApp Cloud API (real)"],
        index=0,
        horizontal=True,
        key="delivery_mode",
        help=(
            "Simulated mode creates a clearly labelled mock delivery record "
            "and never uses the network. Live mode is disabled in this build."
        ),
    )
    real_mode = mode.startswith("WhatsApp")
    if real_mode:
        st.warning(
            "Real delivery is disabled in this build. Environment settings "
            "cannot enable outbound WhatsApp requests. Nothing was sent."
        )
    else:
        st.info(
            "Simulated mode is active. Results are labelled mock and no "
            "external message is sent."
        )

    try:
        all_followup_drafts = db.get_followup_drafts()
        all_deliveries = db.get_message_deliveries(limit=100)
    except Exception as exc:
        st.error(f"Could not load delivery data: {exc}")
        st.stop()

    approved_drafts = [
        row for row in all_followup_drafts if row["Status"] == "APPROVED"
    ]
    if not approved_drafts:
        st.info(
            "No approved follow-up drafts yet. Generate drafts and approve "
            "one above; approval alone sends nothing."
        )
    else:
        approved_labels = {
            f"#{row['Draft']} — {row['Supplier']} (batch "
            f"#{row['Batch']}, run #{row['Run']})": row["Draft"]
            for row in approved_drafts
        }
        approved_label = st.selectbox(
            "Approved draft to preview or simulate",
            options=list(approved_labels),
            key="delivery_draft",
        )
        delivery_draft_id = approved_labels[approved_label]
        delivery_draft = db.get_followup_draft(delivery_draft_id)
        if delivery_draft is not None:
            eligibility = db.get_delivery_eligibility(delivery_draft_id)
            blocking_delivery = db.get_blocking_delivery_for_draft(
                delivery_draft_id
            )
            existing_deliveries = db.get_message_deliveries(
                draft_id=delivery_draft_id, limit=1
            )
            st.markdown(
                f"**Draft #{delivery_draft['id']} — "
                f"{delivery_draft['supplier_name']}**  \n"
                f"Approved: {delivery_draft['approved_at'] or 'unknown'} · "
                f"Recipient snapshot: "
                f"`{delivery_draft['approved_recipient_phone'] or 'missing'}`"
            )
            st.caption("Frozen approved message (a later edit cannot change it):")
            st.text(delivery_draft["approved_text"] or "[snapshot missing]")

            if not eligibility["eligible"]:
                st.warning(
                    "Delivery blocked: "
                    + ", ".join(eligibility["problems"])
                    + ". No message can be sent."
                )
            elif blocking_delivery is not None:
                st.info(
                    f"Delivery #{blocking_delivery['id']} already exists for "
                    f"this draft with status "
                    f"`{blocking_delivery['status']}`. Duplicate sends are "
                    "blocked; use the delivery history below."
                )
            elif existing_deliveries:
                existing_delivery = existing_deliveries[0]
                st.info(
                    f"Delivery #{existing_delivery['id']} is already on "
                    f"record with status `{existing_delivery['status']}`. "
                    "MILAAN keeps one delivery record per approved snapshot "
                    "and does not create a duplicate send; see the delivery "
                    "history and its sanitized error below."
                )
            elif real_mode:
                st.warning(
                    "Real delivery is disabled in this build. Nothing was sent."
                )
            else:
                send_provider = delivery.MockDeliveryProvider()
                send_label = "Create simulated delivery"
                confirmed_send = st.checkbox(
                    (
                        "I confirm the recipient consented and want to "
                        "attempt a REAL WhatsApp message to the recipient "
                        "shown."
                        if real_mode
                        else "I confirm the recipient consented and want "
                        "to create this simulated delivery."
                    ),
                    key=f"delivery_confirm_{delivery_draft_id}",
                )
                if st.button(
                    send_label,
                    key=f"delivery_send_{delivery_draft_id}",
                    disabled=not confirmed_send,
                    type="primary",
                ):
                    try:
                        record = delivery.deliver_approved_draft(
                            delivery_draft_id, send_provider
                        )
                    except (
                        delivery.DeliveryBlockedError,
                        delivery.DeliveryConfigurationError,
                    ) as exc:
                        st.warning(str(exc))
                    except Exception as exc:
                        st.error(f"Delivery attempt failed: {exc}")
                    else:
                        level = (
                            "success"
                            if record["status"] == "SENT"
                            else "warning"
                        )
                        if send_provider.name == delivery.PROVIDER_MOCK:
                            origin = "Simulated delivery"
                            outcome_note = (
                                "No external message was sent (mock)."
                            )
                        else:
                            origin = "Provider request"
                            if record["status"] == "SENT":
                                outcome_note = (
                                    "SENT only means the provider accepted "
                                    "the request; delivery is not confirmed."
                                )
                            else:
                                outcome_note = (
                                    "The request did not produce a confirmed "
                                    "SENT result; inspect the status and "
                                    "sanitized error."
                                )
                        st.session_state["delivery_flash"] = (
                            level,
                            f"{origin} #{record['id']} recorded status "
                            f"{record['status']} with attempt "
                            f"{record['attempt_count']}. {outcome_note}",
                        )
                        st.rerun()

    st.markdown("**Delivery history**")
    if all_deliveries:
        st.dataframe([
            {
                "Delivery": row["id"],
                "Draft": row["followup_draft_id"],
                "Batch": row["gstr2b_import_id"],
                "Supplier": row["supplier_name"],
                "Mode": "simulated" if row["provider"] == "mock" else "real",
                "Provider": row["provider"],
                "Status": row["status"],
                "Attempts": f"{row['attempt_count']}/{row['max_attempts']}",
                "Provider message ID": row["provider_message_id"] or "",
                "Next attempt": row["next_attempt_at"] or "",
                "Updated": row["updated_at"],
                "Last error": (
                    " · ".join(
                        part for part in (
                            row["last_error_code"],
                            row["last_error_message"],
                        ) if part
                    )
                ),
            }
            for row in all_deliveries
        ], alt="Delivery attempts with status and safe provider details",
            hide_index=True)
        resolved_deliveries = [
            row for row in all_deliveries if row["resolution_outcome"]
        ]
        if resolved_deliveries:
            with st.expander("Manual resolution audit"):
                st.dataframe([
                    {
                        "Delivery": row["id"],
                        "Outcome": row["resolution_outcome"],
                        "Resolved by": row["resolved_by"] or "",
                        "Resolved at": row["resolved_at"] or "",
                        "Note": row["resolution_note"] or "",
                        "Provider message ID": (
                            row["provider_message_id"] or ""
                        ),
                    }
                    for row in resolved_deliveries
                ], alt="Operator-provided audit records for resolved deliveries",
                    hide_index=True)
    else:
        st.caption("No delivery attempts recorded yet.")

    retryable = [
        row for row in all_deliveries if row["status"] == "RETRY_PENDING"
    ]
    if retryable:
        st.markdown("**Manual retry**")
        st.caption(
            "Only RETRY_PENDING records can be retried, only when due and "
            "only if attempts remain. Revalidation happens before each "
            "attempt. No scheduler retries anything."
        )
        retry_labels = {
            f"#{row['id']} — {row['supplier_name']} "
            f"({row['attempt_count']}/{row['max_attempts']} attempts, "
            f"next {row['next_attempt_at'] or 'now'})": row["id"]
            for row in retryable
        }
        retry_label = st.selectbox(
            "Retry-pending delivery",
            options=list(retry_labels),
            key="delivery_retry_select",
        )
        retry_id = retry_labels[retry_label]
        retry_record = next(
            row for row in retryable if row["id"] == retry_id
        )
        retry_provider = None
        if retry_record["provider"] == delivery.PROVIDER_MOCK:
            retry_provider = delivery.MockDeliveryProvider()
            st.caption("This retry remains simulated and uses no network.")
        elif not real_mode:
            st.warning(
                "This is a real WhatsApp delivery. Live retries are disabled "
                "in this build."
            )
        else:
            st.warning("Live retries are disabled in this build.")
        confirmed_retry = st.checkbox(
            "I confirm this manual retry is intended and the recipient still "
            "consents.",
            key=f"delivery_retry_confirm_{retry_id}",
            disabled=retry_provider is None,
        )
        if st.button(
            "Retry now (manual)",
            key=f"delivery_retry_{retry_id}",
            disabled=retry_provider is None or not confirmed_retry,
        ):
            try:
                record = delivery.retry_message_delivery(
                    retry_id, retry_provider
                )
            except (
                delivery.DeliveryBlockedError,
                delivery.DeliveryConfigurationError,
            ) as exc:
                st.warning(str(exc))
            except Exception as exc:
                st.error(f"Retry failed: {exc}")
            else:
                if retry_record["provider"] == delivery.PROVIDER_MOCK:
                    retry_note = (
                        " Simulated only; no external message was sent."
                    )
                elif record["status"] == "SENT":
                    retry_note = (
                        " SENT only means the provider accepted the request."
                    )
                else:
                    retry_note = (
                        " The request did not produce a confirmed SENT "
                        "result."
                    )
                st.session_state["delivery_flash"] = (
                    "success" if record["status"] == "SENT" else "warning",
                    f"Manual retry #{record['id']} recorded status "
                    f"{record['status']} with attempt "
                    f"{record['attempt_count']}.{retry_note}",
                )
                st.rerun()

    unknown_deliveries = [
        row for row in all_deliveries if row["status"] == "UNKNOWN"
    ]
    if unknown_deliveries:
        st.markdown("**Resolve UNKNOWN with provider evidence**")
        st.caption(
            "MILAAN never guesses the outcome of an ambiguous request. Use "
            "authoritative provider evidence only. Confirming acceptance "
            "records SENT, never DELIVERED; confirming non-acceptance allows "
            "a bounded manual retry if attempts remain."
        )
        unknown_labels = {
            f"#{row['id']} — {row['supplier_name']} "
            f"(provider {row['provider']})": row["id"]
            for row in unknown_deliveries
        }
        unknown_label = st.selectbox(
            "Unknown delivery",
            options=list(unknown_labels),
            key="delivery_unknown_select",
        )
        unknown_id = unknown_labels[unknown_label]
        resolution = st.radio(
            "Recorded outcome",
            options=["SENT_CONFIRMED", "NOT_SENT"],
            horizontal=True,
            key=f"delivery_resolution_{unknown_id}",
        )
        resolution_by = st.text_input(
            "Resolved by (operator name)",
            key=f"delivery_resolved_by_{unknown_id}",
        )
        resolution_note = st.text_area(
            "Evidence / note",
            key=f"delivery_resolution_note_{unknown_id}",
            height=80,
        )
        manual_message_id = ""
        if resolution == "SENT_CONFIRMED":
            manual_message_id = st.text_input(
                "Provider message ID from Meta",
                key=f"delivery_manual_id_{unknown_id}",
            )
        confirmed_resolution = st.checkbox(
            "I have authoritative provider evidence for this resolution.",
            key=f"delivery_resolution_confirm_{unknown_id}",
        )
        if st.button(
            "Record manual resolution",
            key=f"delivery_resolve_{unknown_id}",
            disabled=not confirmed_resolution,
        ):
            try:
                delivery.resolve_unknown(
                    unknown_id,
                    outcome=resolution,
                    resolved_by=resolution_by,
                    note=resolution_note,
                    provider_message_id=manual_message_id or None,
                )
            except (ValueError, KeyError) as exc:
                st.warning(str(exc))
            except Exception as exc:
                st.error(f"Could not record resolution: {exc}")
            else:
                st.session_state["delivery_flash"] = (
                    "success",
                    f"Delivery #{unknown_id} manually resolved as "
                    f"{resolution}. No message was sent.",
                )
                st.rerun()

    st.caption(
        "Webhook status updates are deferred: DELIVERED/READ require an "
        "authenticated Meta webhook, which this build does not implement."
    )


if __name__ == "__main__":
    main()
