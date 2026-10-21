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
import textwrap
from datetime import datetime
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
import db  # noqa: E402
import delivery  # noqa: E402
import gstr2b  # noqa: E402
import reconciliation  # noqa: E402
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

/* Dashboard surface */
.stApp {
  background: #f6f7f5;
}

.block-container {
  max-width: 1480px;
  padding: 0.4rem 2rem 3.5rem;
}

[data-testid="stHeader"] {
  height: 2.6rem;
}

section[data-testid="stSidebar"] {
  width: 264px !important;
  min-width: 264px !important;
  border-right: 0;
  background: #072e27 !important;
}

section[data-testid="stSidebar"] > div {
  background: transparent !important;
}

[data-testid="stSidebarUserContent"] {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 1.5rem);
  padding: 1.3rem 0.85rem 0.9rem;
}

.sb-brand {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  padding: 0.35rem 0.45rem 1.1rem;
  border-bottom: 1px solid rgba(226, 242, 234, 0.1);
}

.sb-logo {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  flex: 0 0 auto;
  border-radius: 10px;
  background: #e8efe5;
}

.sb-name {
  color: #f4f8f4;
  font-size: 1.65rem;
  font-weight: 600;
  line-height: 1;
  letter-spacing: 0.035em;
}

.sb-sub {
  margin-top: 0.4rem;
  color: #9fb4a8;
  font-size: 0.62rem;
  font-weight: 600;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}

.sb-nav {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin-top: 1.1rem;
}

.sb-nav a {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  min-height: 44px;
  padding: 0.62rem 0.75rem;
  border-radius: 8px;
  color: #c7d6cd;
  font-size: 0.84rem;
  font-weight: 500;
  text-decoration: none !important;
  transition: background-color 150ms ease, color 150ms ease;
}

.sb-nav a:hover {
  background: rgba(255, 255, 255, 0.055);
  color: #ffffff;
}

.sb-nav a.active {
  background: #1c4b3d;
  color: #ffffff;
  font-weight: 600;
}

.sb-nav svg {
  width: 18px;
  height: 18px;
  flex: 0 0 auto;
  opacity: 0.9;
}

.sb-nav a.active svg {
  opacity: 1;
}

.sb-nav-label {
  margin: 1.15rem 0.75rem 0.35rem;
  color: #789287;
  font-size: 0.59rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.sb-env {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  margin: 0.5rem 0.75rem;
  color: #a8bdb2;
  font-size: 0.71rem;
}

.sb-footer {
  margin-top: auto;
  padding: 1rem 0.55rem 0;
  border-top: 1px solid rgba(226, 242, 234, 0.1);
  color: #d2dfd8;
  font-size: 0.74rem;
  line-height: 1.5;
}

.sb-version {
  margin-top: 0.35rem;
  color: #789287;
  font-size: 0.68rem;
}

section[data-testid="stSidebar"] [data-testid="stExpander"] {
  margin-top: 1rem;
  border-color: rgba(226, 242, 234, 0.12);
  background: rgba(255, 255, 255, 0.025);
}

section[data-testid="stSidebar"] [data-testid="stExpander"] summary,
section[data-testid="stSidebar"] [data-testid="stExpander"] p {
  color: #b8ccc1;
  font-size: 0.74rem;
}

section[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover {
  color: #ffffff;
}

section[data-testid="stSidebar"] [data-testid="stDataFrame"] {
  border-color: rgba(226, 242, 234, 0.12);
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1.25rem;
  margin-bottom: 1.35rem;
  padding: 0.95rem 1.2rem;
  border: 1px solid #e3e8e2;
  border-radius: 12px;
  background: #ffffff;
}

.workspace-name {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  color: #15271f;
  font-size: 0.95rem;
  font-weight: 650;
}

.workspace-name svg {
  color: #78867f;
}

.workspace-gstin {
  margin-top: 0.18rem;
  color: #7d8b84;
  font-size: 0.74rem;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 0.7rem;
}

.period-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  padding: 0.52rem 0.68rem;
  border: 1px solid #dfe5df;
  border-radius: 7px;
  background: #ffffff;
  color: #35473f;
  font-size: 0.78rem;
  font-weight: 500;
  white-space: nowrap;
}

.avatar {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: #08392f;
  color: #eaf3ee;
  font-size: 0.85rem;
  font-weight: 650;
}

.page-head {
  margin: 0.3rem 0 1.3rem;
}

.page-title {
  margin: 0;
  color: #10241d;
  font-size: 2.05rem;
  font-weight: 700;
  line-height: 1.08;
  letter-spacing: -0.04em;
}

.page-sub {
  margin-top: 0.35rem;
  color: #6d7c75;
  font-size: 0.92rem;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0.95rem;
}

.kpi-card {
  display: flex;
  align-items: flex-start;
  gap: 0.9rem;
  min-height: 112px;
  padding: 1.05rem 1.15rem;
  border: 1px solid #e3e8e2;
  border-radius: 11px;
  background: #ffffff;
}

.kpi-icon {
  display: grid;
  place-items: center;
  width: 50px;
  height: 50px;
  flex: 0 0 auto;
  border-radius: 50%;
}

.kpi-icon.green {
  background: #e5f1e8;
  color: #0f5d43;
}

.kpi-icon.amber {
  background: #fff1de;
  color: #b37316;
}

.kpi-icon.lavender {
  background: #efedff;
  color: #4f3ca4;
}

.kpi-label {
  color: #6f7d76;
  font-size: 0.76rem;
  font-weight: 500;
}

.kpi-value {
  margin-top: 0.15rem;
  color: #10241d;
  font-size: 1.72rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.15;
  letter-spacing: -0.035em;
}

.kpi-meta {
  margin-top: 0.35rem;
  color: #8a968f;
  font-size: 0.68rem;
  line-height: 1.35;
}

.kpi-meta strong {
  color: #1d7252;
  font-weight: 650;
}

.dash-main {
  display: grid;
  grid-template-columns: minmax(0, 2.15fr) minmax(270px, 1fr);
  gap: 1.1rem;
  margin-top: 1.1rem;
}

.dash-left,
.dash-right {
  display: flex;
  flex-direction: column;
  gap: 1.1rem;
  min-width: 0;
}

.panel {
  overflow: hidden;
  border: 1px solid #e3e8e2;
  border-radius: 11px;
  background: #ffffff;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 1.05rem 1.25rem 0.85rem;
}

.panel-title {
  margin: 0 !important;
  padding-top: 0;
  border-top: 0;
  color: #192b23;
  font-size: 1.02rem !important;
  font-weight: 650 !important;
  letter-spacing: -0.015em;
}

.panel-link {
  color: #0b6048;
  font-size: 0.75rem;
  font-weight: 600;
  text-decoration: none !important;
}

.panel-link:hover {
  color: #0a4c3a;
}

.table-scroll {
  overflow-x: auto;
}

.mismatch-table {
  width: 100%;
  min-width: 780px;
  border-collapse: collapse;
  font-size: 0.78rem;
}

.mismatch-table th {
  padding: 0.62rem 0.8rem;
  border-top: 1px solid #edf0ec;
  border-bottom: 1px solid #edf0ec;
  background: #fafbf9;
  color: #6f7d76;
  font-size: 0.71rem;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}

.mismatch-table th:first-child,
.mismatch-table td:first-child {
  padding-left: 1.25rem;
}

.mismatch-table th:last-child,
.mismatch-table td:last-child {
  padding-right: 1.25rem;
}

.mismatch-table td {
  padding: 0.78rem 0.8rem;
  border-bottom: 1px solid #f0f2ef;
  color: #273b32;
  white-space: nowrap;
}

.mismatch-table tr:last-child td {
  border-bottom: 0;
}

.mismatch-table .invoice-number {
  color: #1c332a;
  font-weight: 600;
}

.mismatch-table .money {
  font-variant-numeric: tabular-nums;
}

.table-action {
  color: #0b6048;
  font-size: 0.75rem;
  font-weight: 600;
  text-decoration: none !important;
}

.table-action:hover {
  text-decoration: underline !important;
}

.empty-row {
  padding: 1.5rem 1.25rem !important;
  color: #7b8982 !important;
  text-align: center !important;
}

.pill {
  display: inline-flex;
  align-items: center;
  padding: 0.24rem 0.46rem;
  border-radius: 5px;
  font-size: 0.67rem;
  font-weight: 600;
  line-height: 1.2;
}

.pill.red {
  background: #fde7e2;
  color: #a64335;
}

.pill.amber {
  background: #fff0d5;
  color: #95631f;
}

.pill.blue {
  background: #e6f1f8;
  color: #316c94;
}

.pill.green {
  background: #e4f2e8;
  color: #25664a;
}

.pill.gray {
  background: #eef0ee;
  color: #697770;
}

.chart-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr);
  gap: 1.1rem;
}

.breakdown-body {
  display: flex;
  align-items: center;
  gap: 1.2rem;
  padding: 0.4rem 1.25rem 1.25rem;
}

.donut-wrap {
  position: relative;
  width: 146px;
  height: 146px;
  flex: 0 0 auto;
}

.donut {
  position: absolute;
  inset: 0;
  border-radius: 50%;
}

.donut::after {
  position: absolute;
  inset: 25px;
  border-radius: 50%;
  background: #ffffff;
  content: "";
}

.donut-center {
  position: absolute;
  inset: 0;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.donut-center strong {
  color: #14271f;
  font-size: 1.45rem;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.03em;
}

.donut-center span {
  margin-top: 0.25rem;
  color: #7d8a83;
  font-size: 0.65rem;
}

.legend {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 0.58rem;
  min-width: 0;
}

.legend-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.5rem;
  color: #45564d;
  font-size: 0.74rem;
}

.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.legend-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.legend-value {
  color: #1b3026;
  font-size: 0.73rem;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}

.trend-legend {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  color: #6e7c75;
  font-size: 0.68rem;
}

.trend-legend span {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}

.trend-key {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.trend-body {
  padding: 0.2rem 1.25rem 1.2rem;
}

.trend-chart {
  display: flex;
  align-items: flex-end;
  gap: 0.7rem;
  height: 172px;
  padding: 0.6rem 0 0;
  background: repeating-linear-gradient(
    to top,
    #edf0ec 0,
    #edf0ec 1px,
    transparent 1px,
    transparent 43px
  );
}

.trend-col {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  height: 100%;
  gap: 0.45rem;
  min-width: 0;
}

.bar-stack {
  display: flex;
  width: min(100%, 32px);
  flex-direction: column;
  justify-content: flex-end;
  overflow: hidden;
  border-radius: 4px 4px 0 0;
}

.bar-mismatch {
  background: #f5cf8b;
}

.bar-matched {
  background: #2d7a59;
}

.trend-label {
  color: #7c8a82;
  font-size: 0.65rem;
}

.quick-list {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  padding: 0 0.9rem 0.95rem;
}

.quick-action {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  padding: 0.72rem 0.75rem;
  border: 1px solid #e5e9e4;
  border-radius: 8px;
  background: #fbfcfa;
  color: #253a30;
  text-decoration: none !important;
  transition: border-color 150ms ease, background-color 150ms ease;
}

.quick-action:hover {
  border-color: #a9c3b5;
  background: #f6faf7;
}

.quick-action svg {
  width: 20px;
  height: 20px;
  flex: 0 0 auto;
  color: #4c685a;
}

.quick-copy {
  min-width: 0;
  flex: 1;
}

.quick-title {
  display: block;
  color: #20362b;
  font-size: 0.83rem;
  font-weight: 600;
  line-height: 1.35;
}

.quick-note {
  display: block;
  margin-top: 0.1rem;
  color: #849189;
  font-size: 0.68rem;
  line-height: 1.35;
}

.quick-arrow {
  color: #8b9891;
  font-size: 0.95rem;
}

.activity-list {
  margin: 0;
  padding: 0 1.25rem 1.15rem;
  list-style: none;
}

.activity-item {
  position: relative;
  padding: 0 0 1.05rem 1.1rem;
  border-left: 1px solid #e5e9e4;
}

.activity-item:last-child {
  padding-bottom: 0;
  border-left-color: transparent;
}

.activity-dot {
  position: absolute;
  top: 0.16rem;
  left: -4px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #6b7d73;
}

.activity-dot.green {
  background: #2d7a59;
}

.activity-dot.amber {
  background: #d4a24a;
}

.activity-dot.blue {
  background: #4b8db8;
}

.activity-title {
  color: #263b31;
  font-size: 0.77rem;
  font-weight: 500;
  line-height: 1.4;
}

.activity-time {
  margin-top: 0.15rem;
  color: #8b9891;
  font-size: 0.66rem;
}

.dash-empty {
  padding: 1.1rem 1.25rem 1.3rem;
  color: #7c8a82;
  font-size: 0.76rem;
  line-height: 1.5;
}

@media (max-width: 1180px) {
  .kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .dash-main {
    grid-template-columns: minmax(0, 1fr);
  }

  .dash-right {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    align-items: start;
  }
}

@media (max-width: 860px) {
  section[data-testid="stSidebar"] {
    width: min(84vw, 300px) !important;
    min-width: 0 !important;
  }

  .block-container {
    padding: 0.35rem 1rem 2.5rem;
  }

  .chart-grid,
  .dash-right {
    grid-template-columns: minmax(0, 1fr);
  }

  .topbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .topbar-actions {
    width: 100%;
    justify-content: space-between;
  }

  .page-title {
    font-size: 1.72rem;
  }

  .breakdown-body {
    align-items: flex-start;
    flex-direction: column;
  }
}

@media (max-width: 560px) {
  .kpi-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .topbar {
    padding: 0.85rem 1rem;
  }

  .mismatch-table {
    min-width: 720px;
  }
}

[data-testid="stToolbarActions"],
[data-testid="stAppDeployButton"],
[data-testid="stStatusWidget"],
[data-testid="stDecoration"],
#MainMenu {
  display: none !important;
  visibility: hidden !important;
}

header[data-testid="stHeader"] {
  height: 0;
  background: transparent;
  pointer-events: none;
}

[data-testid="stExpandSidebarButton"] {
  position: fixed;
  top: 14px;
  left: 16px;
  z-index: 1001;
  width: 40px;
  height: 40px;
  border: 1px solid #dfe5df;
  border-radius: 10px;
  background: #ffffff;
  box-shadow: 0 4px 14px rgba(16, 36, 29, 0.08);
  pointer-events: auto;
}

[data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"] {
  color: #13251f !important;
  font-size: 1.25rem;
}

@media (min-width: 861px) {
  [data-testid="stSidebarCollapseButton"] {
    display: none !important;
  }
}

@media (max-width: 860px) {
  .block-container {
    padding-top: 3.6rem;
  }
}

section[data-testid="stSidebar"] [data-testid="stExpander"] details {
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.035) !important;
}

section[data-testid="stSidebar"] [data-testid="stExpander"] summary {
  background: transparent !important;
}
</style>
"""


def _inject_styles() -> None:
    st.markdown(APP_CSS, unsafe_allow_html=True)


_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

_EVENT_LABELS = {
    "demo_seeded": "Demo workspace seeded",
    "invoices_validated": "Invoice validation completed",
    "reconciliation_completed": "Reconciliation completed",
    "followup_drafts_generated": "Follow-up drafts generated",
    "followup_draft_edited": "Follow-up draft edited",
    "followup_draft_approved": "Follow-up draft approved",
    "followup_draft_skipped": "Follow-up draft skipped",
    "later_statement_checked": "Later-statement check recorded",
}

_STATUS_DISPLAY = {
    reconciliation.STATUS_MISSING_FROM_2B: (
        "Not in GSTR-2B", "Missing", "red",
    ),
    reconciliation.STATUS_AMOUNT_MISMATCH: (
        "Amount mismatch", "Mismatch", "amber",
    ),
    reconciliation.STATUS_NEEDS_REVIEW: (
        "Needs review", "Review", "blue",
    ),
    reconciliation.STATUS_NOT_ELIGIBLE: (
        "Not eligible", "Excluded", "gray",
    ),
}


def _inr(amount: float) -> str:
    rounded = int(round(float(amount)))
    sign = "-" if rounded < 0 else ""
    digits = str(abs(rounded))
    if len(digits) > 3:
        last_three = digits[-3:]
        rest = digits[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        digits = ",".join(groups + [last_three])
    return f"{sign}₹{digits}"


def _parse_amount(value) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _display_date(value) -> str:
    raw = str(value or "")[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%d %b %Y")
    except ValueError:
        return raw


def _display_time(value) -> str:
    raw = str(value or "")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).strftime("%d %b %Y · %H:%M")
        except ValueError:
            continue
    return _display_date(value)


def _period_label(period: str) -> str:
    try:
        year, month = str(period).split("-")[:2]
        return _MONTHS[int(month) - 1]
    except (ValueError, IndexError):
        return str(period)


def _activity_label(event_type: str) -> str:
    if event_type in _EVENT_LABELS:
        return _EVENT_LABELS[event_type]
    return event_type.replace("_", " ").capitalize()


def _load_dashboard() -> dict:
    invoices = db.get_invoices()
    suppliers = db.get_suppliers()
    latest_run = db.get_latest_reconciliation_run()
    summary = (
        db.get_reconciliation_summary(latest_run["id"])
        if latest_run is not None else None
    )
    results = (
        db.get_reconciliation_results(latest_run["id"])
        if latest_run is not None else []
    )
    invoice_by_id = {row["ID"]: row for row in invoices}
    supplier_gstin = {
        row["Supplier"]: row["GSTIN"] for row in suppliers
    }
    audit_events = db.get_system_audit_events(limit=5)

    has_run = summary is not None
    validation_issues = [
        row for row in invoices
        if row["Validation"] in ("invalid", "needs_review")
    ]

    if has_run:
        by_status = summary["by_status"]
        matched = by_status[reconciliation.STATUS_MATCHED]["count"]
        exception_total = sum(
            item["count"] for status, item in by_status.items()
            if status != reconciliation.STATUS_MATCHED
        )
        gst_exceptions = sum(
            item["total_gst_paise"] for status, item in by_status.items()
            if status != reconciliation.STATUS_MATCHED
        ) / 100
    else:
        matched = 0
        exception_total = len(validation_issues)
        gst_exceptions = sum(
            _parse_amount(row["Total GST (Rs)"])
            for row in validation_issues
        )

    if has_run:
        category_specs = [
            (
                reconciliation.STATUS_MISSING_FROM_2B,
                "Not in GSTR-2B",
                "#ef7b68",
            ),
            (
                reconciliation.STATUS_AMOUNT_MISMATCH,
                "Amount mismatch",
                "#f0c35c",
            ),
            (
                reconciliation.STATUS_NEEDS_REVIEW,
                "Needs review",
                "#5aa8d8",
            ),
            (
                reconciliation.STATUS_NOT_ELIGIBLE,
                "Not eligible",
                "#7eab93",
            ),
        ]
        categories = [
            {
                "label": label,
                "count": by_status[status]["count"],
                "color": color,
            }
            for status, label, color in category_specs
        ]
    else:
        categories = [
            {
                "label": "Validation failed",
                "count": sum(
                    row["Validation"] == "invalid" for row in invoices
                ),
                "color": "#ef7b68",
            },
            {
                "label": "Needs review",
                "count": sum(
                    row["Validation"] == "needs_review" for row in invoices
                ),
                "color": "#f0c35c",
            },
        ]

    for category in categories:
        category["pct"] = (
            category["count"] / exception_total * 100
            if exception_total else 0
        )

    recent = []
    if has_run:
        for result in results:
            status = result["Status"]
            if status == reconciliation.STATUS_MATCHED:
                continue
            invoice = invoice_by_id.get(result["Invoice #"], {})
            issue, pill, tone = _STATUS_DISPLAY.get(
                status, (status, status, "gray")
            )
            recent.append({
                "sort_date": str(invoice.get("Date", "")),
                "invoice": result["Invoice number"],
                "date": _display_date(invoice.get("Date")),
                "gstin": (
                    result["GSTIN"]
                    or supplier_gstin.get(invoice.get("Supplier"), "")
                ),
                "issue": issue,
                "amount": _parse_amount(result["Invoice GST (Rs)"]),
                "pill": pill,
                "tone": tone,
            })
    else:
        for invoice in validation_issues:
            invalid = invoice["Validation"] == "invalid"
            recent.append({
                "sort_date": str(invoice.get("Date", "")),
                "invoice": invoice["Invoice"],
                "date": _display_date(invoice.get("Date")),
                "gstin": supplier_gstin.get(invoice.get("Supplier"), ""),
                "issue": "Validation failed" if invalid else "Needs review",
                "amount": _parse_amount(invoice["Total GST (Rs)"]),
                "pill": "Invalid" if invalid else "Review",
                "tone": "red" if invalid else "amber",
            })

    recent.sort(key=lambda item: item["sort_date"], reverse=True)
    recent = recent[:5]

    trend = []
    if has_run:
        buckets = {}
        for result in results:
            invoice = invoice_by_id.get(result["Invoice #"], {})
            period = str(invoice.get("Tax period", "") or "")
            if not period:
                continue
            bucket = buckets.setdefault(
                period, {"matched": 0, "exceptions": 0}
            )
            if result["Status"] == reconciliation.STATUS_MATCHED:
                bucket["matched"] += 1
            else:
                bucket["exceptions"] += 1
        trend = [
            {
                "period": period,
                "label": _period_label(period),
                "matched": bucket["matched"],
                "exceptions": bucket["exceptions"],
                "total": bucket["matched"] + bucket["exceptions"],
            }
            for period, bucket in sorted(buckets.items())
        ]

    activity = []
    for event in audit_events:
        event_type = str(event["Event"])
        if "followup" in event_type:
            color = "amber"
        elif "later" in event_type:
            color = "blue"
        elif "reconciliation" in event_type or "delivery" in event_type:
            color = "green"
        else:
            color = "gray"
        activity.append({
            "label": _activity_label(event_type),
            "time": _display_time(event["At"]),
            "color": color,
        })

    return {
        "total_invoices": len(invoices),
        "supplier_count": len(suppliers),
        "matched": matched,
        "exceptions": exception_total,
        "gst_exceptions": gst_exceptions,
        "categories": categories,
        "recent": recent,
        "trend": trend,
        "activity": activity,
        "has_run": has_run,
        "run_id": latest_run["id"] if latest_run is not None else None,
    }


def _render_ui_html(markup: str) -> None:
    compact = " ".join(
        line.strip()
        for line in textwrap.dedent(markup).splitlines()
        if line.strip()
    )
    st.markdown(compact, unsafe_allow_html=True)


def _refresh_dashboard(slot) -> None:
    try:
        data = _load_dashboard()
    except Exception:
        return
    with slot.container():
        _render_dashboard(data)


def _render_topbar(actor: str) -> None:
    workspace = "Demo Business" if actor == "local-demo" else actor
    initial = "D" if actor == "local-demo" else (actor[:1].upper() or "M")
    _render_ui_html(
        f"""
        <div class="topbar">
          <div>
            <div class="workspace-name">
              {html.escape(workspace)}
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none"
                   aria-hidden="true">
                <path d="m4 6 4 4 4-4" stroke="currentColor"
                      stroke-width="1.5" stroke-linecap="round"
                      stroke-linejoin="round"/>
              </svg>
            </div>
            <div class="workspace-gstin">
              Synthetic workspace · Live sending off
            </div>
          </div>
          <div class="topbar-actions">
            <span class="period-chip">
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none"
                   aria-hidden="true">
                <rect x="2.5" y="3.5" width="11" height="10"
                      rx="1.5" stroke="currentColor" stroke-width="1.2"/>
                <path d="M5 2.5v2M11 2.5v2M2.5 6.5h11"
                      stroke="currentColor" stroke-width="1.2"
                      stroke-linecap="round"/>
              </svg>
              All periods
            </span>
            <span class="avatar">{html.escape(initial)}</span>
          </div>
        </div>
        """,
    )


def _render_dashboard(data: dict) -> None:
    total = data["total_invoices"]
    exceptions = data["exceptions"]
    matched = data["matched"]
    gst_exceptions = data["gst_exceptions"]
    run_note = (
        f"Latest run #{data['run_id']}" if data["has_run"]
        else "Validation view · no run yet"
    )

    recent_rows = "".join(
        f"""
        <tr>
          <td class="invoice-number">{html.escape(str(item["invoice"]))}</td>
          <td>{html.escape(item["date"])}</td>
          <td>{html.escape(item["gstin"] or "—")}</td>
          <td>{html.escape(item["issue"])}</td>
          <td class="money">
            {html.escape(_inr(item["amount"]).replace("₹", ""))}
          </td>
          <td><span class="pill {item["tone"]}">
            {html.escape(item["pill"])}
          </span></td>
          <td><a class="table-action" href="#reconciliation">View</a></td>
        </tr>
        """
        for item in data["recent"]
    )
    if not recent_rows:
        recent_rows = (
            '<tr><td class="empty-row" colspan="7">'
            "No open exceptions in the current view."
            "</td></tr>"
        )

    if exceptions:
        cursor = 0.0
        segments = []
        for category in data["categories"]:
            start = cursor
            cursor = min(100.0, cursor + category["pct"])
            segments.append(
                f"{category['color']} {start:.2f}% {cursor:.2f}%"
            )
        donut_style = f"background: conic-gradient({', '.join(segments)});"
    else:
        donut_style = "background: #edf0ed;"

    legend_rows = "".join(
        f"""
        <div class="legend-row">
          <span class="legend-dot" style="background:{category['color']}"></span>
          <span class="legend-label">{html.escape(category["label"])}</span>
          <span class="legend-value">{category["pct"]:.0f}%</span>
        </div>
        """
        for category in data["categories"]
    )
    if not legend_rows:
        legend_rows = (
            '<div class="legend-row"><span></span>'
            "<span class=\"legend-label\">No open exceptions</span>"
            '<span class="legend-value">0%</span></div>'
        )

    if data["trend"]:
        max_total = max(item["total"] for item in data["trend"]) or 1
        trend_columns = []
        for point in data["trend"]:
            height = max(8, round(point["total"] / max_total * 128))
            matched_height = round(
                point["matched"] / point["total"] * height
            ) if point["total"] else 0
            exception_height = max(0, height - matched_height)
            trend_columns.append(
                f"""
                <div class="trend-col">
                  <div class="bar-stack" style="height:{height}px">
                    <div class="bar-mismatch"
                         style="height:{exception_height}px"></div>
                    <div class="bar-matched"
                         style="height:{matched_height}px"></div>
                  </div>
                  <span class="trend-label">{html.escape(point["label"])}</span>
                </div>
                """
            )
        trend_html = (
            '<div class="trend-chart">'
            + "".join(trend_columns)
            + "</div>"
        )
    else:
        trend_html = (
            '<div class="dash-empty">Run reconciliation to populate the '
            "monthly trend.</div>"
        )

    if data["activity"]:
        activity_html = "".join(
            f"""
            <li class="activity-item">
              <span class="activity-dot {item["color"]}"></span>
              <div class="activity-title">{html.escape(item["label"])}</div>
              <div class="activity-time">{html.escape(item["time"])}</div>
            </li>
            """
            for item in data["activity"]
        )
    else:
        activity_html = (
            '<li class="activity-item"><span class="activity-dot"></span>'
            '<div class="activity-title">No activity recorded yet.</div>'
            '<div class="activity-time">Seed demo data or upload a file to '
            "begin.</div></li>"
        )

    _render_ui_html(
        f"""
        <div id="dashboard">
          <div class="page-head">
            <h1 class="page-title">Dashboard</h1>
            <div class="page-sub">Overview of your ITC reconciliation</div>
          </div>

          <div class="kpi-grid">
            <div class="kpi-card">
              <div class="kpi-icon green">
                <svg width="25" height="25" viewBox="0 0 24 24"
                     fill="none" aria-hidden="true">
                  <path d="M6 3.5h7l5 5V21H6z" stroke="currentColor"
                        stroke-width="1.6" stroke-linejoin="round"/>
                  <path d="M13 3.5v5h5M9 13h6M9 16.5h4"
                        stroke="currentColor" stroke-width="1.6"
                        stroke-linecap="round"/>
                </svg>
              </div>
              <div>
                <div class="kpi-label">Total invoices</div>
                <div class="kpi-value">{total:,}</div>
                <div class="kpi-meta">{data["supplier_count"]} suppliers
                  recorded</div>
              </div>
            </div>

            <div class="kpi-card">
              <div class="kpi-icon amber">
                <svg width="25" height="25" viewBox="0 0 24 24"
                     fill="none" aria-hidden="true">
                  <path d="M12 4 21 20H3L12 4Z" stroke="currentColor"
                        stroke-width="1.6" stroke-linejoin="round"/>
                  <path d="M12 9v5M12 17h.01" stroke="currentColor"
                        stroke-width="1.7" stroke-linecap="round"/>
                </svg>
              </div>
              <div>
                <div class="kpi-label">Mismatches</div>
                <div class="kpi-value">{exceptions:,}</div>
                <div class="kpi-meta">{html.escape(run_note)}</div>
              </div>
            </div>

            <div class="kpi-card">
              <div class="kpi-icon green">
                <svg width="25" height="25" viewBox="0 0 24 24"
                     fill="none" aria-hidden="true">
                  <circle cx="12" cy="12" r="8.5" stroke="currentColor"
                          stroke-width="1.6"/>
                  <path d="m8.5 12 2.3 2.3 4.7-5"
                        stroke="currentColor" stroke-width="1.7"
                        stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
              </div>
              <div>
                <div class="kpi-label">Reconciled</div>
                <div class="kpi-value">{matched:,}</div>
                <div class="kpi-meta">Exact evidence matched</div>
              </div>
            </div>

            <div class="kpi-card">
              <div class="kpi-icon lavender">
                <span style="font-size:1.38rem;font-weight:600">₹</span>
              </div>
              <div>
                <div class="kpi-label">GST in exceptions</div>
                <div class="kpi-value">{html.escape(_inr(gst_exceptions))}</div>
                <div class="kpi-meta">Not a recovery or eligibility claim</div>
              </div>
            </div>
          </div>

          <div class="dash-main">
            <div class="dash-left">
              <section class="panel">
                <div class="panel-head">
                  <h2 class="panel-title">Recent Mismatches</h2>
                  <a class="panel-link" href="#reconciliation">View All →</a>
                </div>
                <div class="table-scroll">
                  <table class="mismatch-table">
                    <thead>
                      <tr>
                        <th>Invoice No.</th>
                        <th>Date</th>
                        <th>Supplier GSTIN</th>
                        <th>Issue Type</th>
                        <th>Amount (₹)</th>
                        <th>Status</th>
                        <th>Action</th>
                      </tr>
                    </thead>
                    <tbody>{recent_rows}</tbody>
                  </table>
                </div>
              </section>

              <div class="chart-grid">
                <section class="panel">
                  <div class="panel-head">
                    <h2 class="panel-title">Mismatch Breakdown</h2>
                  </div>
                  <div class="breakdown-body">
                    <div class="donut-wrap">
                      <div class="donut" style="{donut_style}"></div>
                      <div class="donut-center">
                        <strong>{exceptions}</strong>
                        <span>exceptions</span>
                      </div>
                    </div>
                    <div class="legend">{legend_rows}</div>
                  </div>
                </section>

                <section class="panel">
                  <div class="panel-head">
                    <h2 class="panel-title">Monthly Trend</h2>
                    <div class="trend-legend">
                      <span><i class="trend-key"
                        style="background:#2d7a59"></i>Reconciled</span>
                      <span><i class="trend-key"
                        style="background:#f5cf8b"></i>Exceptions</span>
                    </div>
                  </div>
                  <div class="trend-body">{trend_html}</div>
                </section>
              </div>
            </div>

            <div class="dash-right">
              <section class="panel">
                <div class="panel-head">
                  <h2 class="panel-title">Quick Actions</h2>
                </div>
                <div class="quick-list">
                  <a class="quick-action" href="#import-gstr2b">
                    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                      <path d="M6 3.5h7l5 5V21H6z" stroke="currentColor"
                            stroke-width="1.5" stroke-linejoin="round"/>
                      <path d="M13 3.5v5h5M12 17v-5M9.5 14.5 12 17l2.5-2.5"
                            stroke="currentColor" stroke-width="1.5"
                            stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    <span class="quick-copy">
                      <span class="quick-title">Upload Purchase Data</span>
                      <span class="quick-note">CSV or Excel</span>
                    </span>
                    <span class="quick-arrow">›</span>
                  </a>
                  <a class="quick-action" href="#reconciliation">
                    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                      <path d="M7 8h10M7 8l2.2-2.2M7 8l2.2 2.2M17 16H7M17 16l-2.2-2.2M17 16l-2.2 2.2"
                            stroke="currentColor" stroke-width="1.5"
                            stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    <span class="quick-copy">
                      <span class="quick-title">Run Reconciliation</span>
                      <span class="quick-note">Match with GSTR-2B</span>
                    </span>
                    <span class="quick-arrow">›</span>
                  </a>
                  <a class="quick-action" href="#follow-ups">
                    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                      <path d="m4 11 16-7-6 16-2.7-6.3L4 11Z"
                            stroke="currentColor" stroke-width="1.5"
                            stroke-linejoin="round"/>
                      <path d="m11.3 13.7 3-3" stroke="currentColor"
                            stroke-width="1.5" stroke-linecap="round"/>
                    </svg>
                    <span class="quick-copy">
                      <span class="quick-title">Review Follow-ups</span>
                      <span class="quick-note">Drafts and pending approval</span>
                    </span>
                    <span class="quick-arrow">›</span>
                  </a>
                  <a class="quick-action" href="#delivery">
                    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                      <rect x="4" y="3.5" width="16" height="17" rx="2"
                            stroke="currentColor" stroke-width="1.5"/>
                      <path d="M8 8h8M8 12h8M8 16h5" stroke="currentColor"
                            stroke-width="1.5" stroke-linecap="round"/>
                    </svg>
                    <span class="quick-copy">
                      <span class="quick-title">Open Delivery Audit</span>
                      <span class="quick-note">Mock sends and evidence</span>
                    </span>
                    <span class="quick-arrow">›</span>
                  </a>
                </div>
              </section>

              <section class="panel">
                <div class="panel-head">
                  <h2 class="panel-title">Recent Activity</h2>
                  <a class="panel-link" href="#delivery">View All →</a>
                </div>
                <ul class="activity-list">{activity_html}</ul>
              </section>
            </div>
          </div>
        </div>
        """,
    )


def _render_sidebar(ready: bool, actor: str) -> None:
    database_path = html.escape(str(db.get_database_path().resolve()))
    status_text = "Database ready" if ready else "Database attention needed"
    with st.sidebar:
        _render_ui_html(
            """
            <div class="sb-brand">
              <div class="sb-logo" aria-hidden="true">
                <svg width="26" height="26" viewBox="0 0 32 32" fill="none">
                  <path d="M16 27C8.3 23.5 5 17.8 6.7 10.3 14 10 20 14.7 21.3 22.2c.2 2-1.7 4-5.3 4.8Z"
                        fill="#5f8b65"/>
                  <path d="M16.7 27c-.5-8.5 4.2-14.5 10.3-17.2 2.1 8.3-2.7 14.7-10.3 17.2Z"
                        fill="#b7cbb1"/>
                </svg>
              </div>
              <div>
                <div class="sb-name">MILAAN</div>
                <div class="sb-sub">ITC Reconciliation</div>
              </div>
            </div>

            <nav class="sb-nav" aria-label="Workspace">
              <a class="active" href="#dashboard">
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="M4 10.5 12 4l8 6.5V20H4z" stroke="currentColor"
                        stroke-width="1.6" stroke-linejoin="round"/>
                  <path d="M9.5 20v-5.5h5V20" stroke="currentColor"
                        stroke-width="1.6" stroke-linejoin="round"/>
                </svg>
                Dashboard
              </a>
              <a href="#import-gstr2b">
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="M6 3.5h7l5 5V21H6z" stroke="currentColor"
                        stroke-width="1.5" stroke-linejoin="round"/>
                  <path d="M13 3.5v5h5M12 17v-5M9.5 14.5 12 17l2.5-2.5"
                        stroke="currentColor" stroke-width="1.5"
                        stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
                Upload Data
              </a>
              <a href="#reconciliation">
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="M12 4 21 20H3L12 4Z" stroke="currentColor"
                        stroke-width="1.5" stroke-linejoin="round"/>
                  <path d="M12 9v5M12 17h.01" stroke="currentColor"
                        stroke-width="1.6" stroke-linecap="round"/>
                </svg>
                Mismatches
              </a>
              <a href="#follow-ups">
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="m4 11 16-7-6 16-2.7-6.3L4 11Z"
                        stroke="currentColor" stroke-width="1.5"
                        stroke-linejoin="round"/>
                  <path d="m11.3 13.7 3-3" stroke="currentColor"
                        stroke-width="1.5" stroke-linecap="round"/>
                </svg>
                Follow-ups
              </a>
              <a href="#suppliers">
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <circle cx="9" cy="8.5" r="3" stroke="currentColor"
                          stroke-width="1.5"/>
                  <path d="M4 19c.4-3 2.3-4.8 5-4.8s4.6 1.8 5 4.8"
                        stroke="currentColor" stroke-width="1.5"
                        stroke-linecap="round"/>
                  <path d="M16 6.2a2.6 2.6 0 0 1 0 5M17.5 14.5c1.6.5 2.5 2 2.8 4.5"
                        stroke="currentColor" stroke-width="1.5"
                        stroke-linecap="round"/>
                </svg>
                Suppliers
              </a>
              <a href="#delivery">
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <rect x="4" y="3.5" width="16" height="17" rx="2"
                        stroke="currentColor" stroke-width="1.5"/>
                  <path d="M8 8h8M8 12h8M8 16h5" stroke="currentColor"
                        stroke-width="1.5" stroke-linecap="round"/>
                </svg>
                Audit Log
              </a>
              <a href="#settings">
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <circle cx="12" cy="12" r="3" stroke="currentColor"
                          stroke-width="1.5"/>
                  <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06-2.1 2.1-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V20.3h-2.97v-.11a1.7 1.7 0 0 0-1.03-1.56 1.7 1.7 0 0 0-1.87.34l-.06.06-2.1-2.1.06-.06A1.7 1.7 0 0 0 7.1 15a1.7 1.7 0 0 0-1.56-1.03H5.4v-2.97h.14A1.7 1.7 0 0 0 7.1 9.97a1.7 1.7 0 0 0-.34-1.87L6.7 8.04l2.1-2.1.06.06a1.7 1.7 0 0 0 1.87.34 1.7 1.7 0 0 0 1.03-1.56V4.7h2.97v.08a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06 2.1 2.1-.06.06a1.7 1.7 0 0 0-.34 1.87 1.7 1.7 0 0 0 1.56 1.03h.11v2.97h-.11A1.7 1.7 0 0 0 19.4 15Z"
                        stroke="currentColor" stroke-width="1.25"
                        stroke-linejoin="round"/>
                </svg>
                Settings
              </a>
            </nav>
            """,
        )
        with st.expander("Operational audit"):
            try:
                audit_events = db.get_system_audit_events()
            except Exception as exc:
                st.error(f"Could not load audit history: {exc}")
            else:
                st.caption(status_text)
                st.caption(f"Database: {database_path}")
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
        _render_ui_html(
            """
            <div class="sb-footer">
              Match. Detect. Resolve.
              <div class="sb-version">v1.0.0 · Live sending off</div>
            </div>
            """,
        )


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

    try:
        dashboard = _load_dashboard()
    except Exception as exc:
        _render_topbar(actor)
        _render_sidebar(ready, actor)
        st.error(f"Could not load dashboard: {exc}")
        st.stop()

    _render_topbar(actor)
    _render_sidebar(ready, actor)
    dashboard_slot = st.empty()
    with dashboard_slot.container():
        _render_dashboard(dashboard)

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
        _refresh_dashboard(dashboard_slot)

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
            _refresh_dashboard(dashboard_slot)
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

    st.markdown('<div id="suppliers"></div>', unsafe_allow_html=True)
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
                _refresh_dashboard(dashboard_slot)
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
                _refresh_dashboard(dashboard_slot)

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

    st.header(
        "Settings & limits",
        icon=":material/settings:",
        anchor="settings",
    )
    st.caption(
        "This build is intentionally read-only outside the workflow above. "
        "No setting can enable live WhatsApp delivery."
    )
    settings_columns = st.columns(3)
    settings_columns[0].markdown(
        "**Data**  \n"
        "Synthetic demo records only. GSTIN checks are structural; live "
        "registration is not queried."
    )
    settings_columns[1].markdown(
        "**Matching**  \n"
        "Exact GSTIN + invoice + period keys. Ambiguous evidence is routed "
        "to human review."
    )
    settings_columns[2].markdown(
        "**Delivery**  \n"
        "Mock-only. Approval records intent and never sends a message."
    )


if __name__ == "__main__":
    main()
