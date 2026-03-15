from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from string import Template
from urllib import request as urlrequest

import streamlit as st

from esg_analyzer.batch import analyze_batch
from esg_analyzer.diff import compute_diff_report
from esg_analyzer.llm_provider import LLMConfig, get_llm_status
from esg_analyzer.pipeline import run_pipeline
from esg_analyzer.report.diff_report import generate_diff_report
from esg_analyzer.utils.names import clean_company_name


def _hydrate_env_from_secrets() -> None:
    """
    Map Streamlit secrets to environment variables for LLMConfig.
    Keeps keys out of the UI and works on Streamlit Cloud.
    """
    secrets_paths = (
        Path(".streamlit/secrets.toml"),
        Path.home() / ".streamlit" / "secrets.toml",
    )
    if not any(path.exists() for path in secrets_paths):
        return
    try:
        secrets = st.secrets
    except Exception:
        return

    keys = (
        "LLM_PROVIDER",
        "LLM_MODEL",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
    )
    for key in keys:
        try:
            if key in secrets and not os.environ.get(key):
                os.environ[key] = str(secrets[key])
        except Exception:
            return


def _output_root() -> Path:
    root = Path(os.getenv("GIRAFON_OUTPUT_DIR", Path.cwd() / "outputs"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _auto_chunk_settings(speed_reliability: int) -> tuple[int, int, int]:
    """Map a speed↔reliability slider to chunking defaults."""
    speed_cfg = {"chunk": 700, "overlap": 60, "min_chunk": 60}
    reliable_cfg = {"chunk": 420, "overlap": 160, "min_chunk": 30}
    t = max(0, min(100, speed_reliability)) / 100.0

    def _lerp(a: int, b: int) -> int:
        return int(round(a + (b - a) * t))

    chunk_words = _lerp(speed_cfg["chunk"], reliable_cfg["chunk"])
    overlap_words = _lerp(speed_cfg["overlap"], reliable_cfg["overlap"])
    min_chunk_words = _lerp(speed_cfg["min_chunk"], reliable_cfg["min_chunk"])

    overlap_words = min(overlap_words, max(0, chunk_words - 50))
    min_chunk_words = min(min_chunk_words, max(10, chunk_words - 100))
    return chunk_words, overlap_words, min_chunk_words


def main() -> None:
    st.set_page_config(page_title="Girafon — ESRS Gap Detector", page_icon="🦒", layout="wide")
    _hydrate_env_from_secrets()
    if "report_history" not in st.session_state:
        st.session_state.report_history = []
    if "selected_report_id" not in st.session_state:
        st.session_state.selected_report_id = None
    if "run_state" not in st.session_state:
        st.session_state.run_state = {
            "running": False,
            "mode": "",
            "logs": [],
            "progress": 0.0,
            "last_msg": "",
            "status": "idle",
            "error": "",
            "started_at": "",
            "finished_at": "",
            "updated_at": 0.0,
            "run_id": "",
            "llm_provider": "",
            "llm_model": "",
            "llm_location": "",
            "meta": {},
        }
    if "auto_tune_chunking" not in st.session_state:
        st.session_state.auto_tune_chunking = True
    if "speed_reliability" not in st.session_state:
        st.session_state.speed_reliability = 60
    if "chunk_words" not in st.session_state:
        st.session_state.chunk_words = 500
    if "overlap_words" not in st.session_state:
        st.session_state.overlap_words = 120
    if "min_chunk_words" not in st.session_state:
        st.session_state.min_chunk_words = 40
    theme_choice = st.session_state.get("theme_toggle", False)
    is_dark = bool(theme_choice)
    st.session_state.theme_mode = "dark" if is_dark else "light"
    theme_vars = {
        "ink": "#f7efe5" if is_dark else "#24180f",
        "brown": "#d79a5f" if is_dark else "#a1581f",
        "tan": "#a9784f" if is_dark else "#d6b18d",
        "cream": "#1c1612" if is_dark else "#f6f2ed",
        "sand": "#2d241d" if is_dark else "#e6d9cc",
        "mist": "#231b15" if is_dark else "#f2e7da",
        "accent": "#d37a3b" if is_dark else "#c56a28",
        "accent_dark": "#b9642f" if is_dark else "#a25422",
        "accent_pressed": "#9e5527" if is_dark else "#87461b",
        "shadow": "rgba(0, 0, 0, 0.3)" if is_dark else "rgba(30, 24, 18, 0.08)",
        "sidebar": "#1f1813" if is_dark else "#f2e7da",
        "surface": "#241c16" if is_dark else "#ffffff",
        "border": "#4b392c" if is_dark else "#d8c4b0",
        "muted": "#cbb8a5" if is_dark else "#7a6450",
        "file_bg": "#241c16" if is_dark else "#ffffff",
        "file_border": "#4b392c" if is_dark else "#d8c4b0",
        "file_text": "#f3e9df" if is_dark else "#3a2a1e",
        "file_btn_bg": "#f3e9df" if is_dark else "#f7efe6",
        "file_btn_text": "#2b1f16" if is_dark else "#3a2a1e",
        "file_btn_hover": "#e7d6c6" if is_dark else "#efe3d6",
        "link": "#e1a86a" if is_dark else "#8b5a2b",
        "alert_bg": "#2a211a" if is_dark else "#fff6ec",
        "alert_border": "#4b392c" if is_dark else "#e2d1c1",
        "code_bg": "#1f1813" if is_dark else "#fbf7f2",
        "disabled_bg": "#3a2b22" if is_dark else "#e0d2c5",
        "disabled_text": "#cbb8a5" if is_dark else "#5a4638",
    }
    css = Template(
        """
<style>
          @import url('https://api.fontshare.com/v2/css?f[]=satoshi@400,500,600,700&display=swap');
          @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap');

          /* Root tokens (prefer Streamlit theme vars; fall back to template values) */
          :root {
            --giraffe-ink: var(--text-color, $ink);
            --giraffe-brown: $brown;
            --giraffe-tan: $tan;
            --giraffe-cream: var(--background-color, $cream);
            --giraffe-sand: var(--secondary-background-color, $sand);
            --giraffe-mist: $mist;
            --giraffe-accent: var(--primary-color, $accent);
            --giraffe-accent-dark: $accent_dark;
            --giraffe-accent-pressed: $accent_pressed;
            --giraffe-shadow: $shadow;
            --giraffe-sidebar: var(--secondary-background-color, $sidebar);
            --giraffe-surface: var(--secondary-background-color, $surface);
            --giraffe-border: $border;
            --giraffe-muted: $muted;
            --giraffe-file-bg: $file_bg;
            --giraffe-file-border: $file_border;
            --giraffe-file-text: $file_text;
            --giraffe-file-btn-bg: $file_btn_bg;
            --giraffe-file-btn-text: $file_btn_text;
            --giraffe-file-btn-hover: $file_btn_hover;
            --giraffe-link: $link;
            --giraffe-alert-bg: $alert_bg;
            --giraffe-alert-border: $alert_border;
            --giraffe-code-bg: $code_bg;
            --giraffe-disabled-bg: #c9b8a8;
            --giraffe-disabled-text: #3a2a1e;
          }

          /* Layout */
          html, body, [class*="stApp"] {
            font-family: var(--font, 'Inter', sans-serif);
            color: var(--giraffe-ink);
            background: var(--giraffe-cream);
            font-size: 15px;
            line-height: 1.6;
          }

          /* Typography */
          h1, h2, h3, h4, h5, h6,
          .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
            font-family: 'Satoshi', sans-serif;
            color: var(--giraffe-ink);
            letter-spacing: 0.3px;
          }
          .eyebrow {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            font-family: 'Satoshi', sans-serif;
            text-transform: uppercase;
            letter-spacing: 0.16em;
            font-size: 12px;
            font-weight: 600;
            color: var(--giraffe-accent);
            margin: 0 0 6px;
          }

          /* Sidebar */
          [data-testid="stSidebar"] {
            background: var(--giraffe-sidebar);
            border-right: 1px solid var(--giraffe-border);
          }
          [data-testid="stSidebar"] * {
            color: var(--giraffe-ink);
          }
          [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
          [data-testid="stSidebar"] label,
          [data-testid="stSidebar"] .stMarkdown p {
            color: var(--giraffe-ink);
            font-weight: 600;
          }
          [data-testid="stWidgetLabel"] p,
          [data-testid="stWidgetLabel"] label,
          [data-testid="stWidgetLabel"] span {
            color: var(--giraffe-ink);
          }
          [data-testid="stWidgetLabel"],
          [data-testid="stWidgetLabel"] > div,
          [data-testid="stWidgetLabel"] [data-testid="stMarkdownContainer"],
          [data-testid="stWidgetLabel"] div,
          [data-testid="stWidgetLabel"] p,
          [data-testid="stWidgetLabel"] span {
            background: transparent !important;
          }
          /* Toggle label wrappers (Streamlit 1.55.0, BaseWeb DOM; fragile selector) */
          .stToggle [data-testid="stWidgetLabel"],
          [data-testid="stToggle"] [data-testid="stWidgetLabel"],
          .stToggle [data-testid="stMarkdownContainer"],
          [data-testid="stToggle"] [data-testid="stMarkdownContainer"],
          .stToggle label,
          [data-testid="stToggle"] label,
          .stToggle p,
          [data-testid="stToggle"] p {
            background: transparent !important;
            box-shadow: none !important;
          }
          .stToggle div:not([data-baseweb="toggle"]):not([role="switch"]),
          [data-testid="stToggle"] div:not([data-baseweb="toggle"]):not([role="switch"]) {
            background: transparent !important;
          }
          /* Checkbox/toggle label wrapper = input + div (Streamlit 1.55.0, BaseWeb DOM; fragile selector) */
          [data-testid="stCheckbox"] label input + div,
          [data-testid="stToggle"] label input + div,
          .stToggle label input + div {
            background: transparent !important;
            box-shadow: none !important;
          }

          /* Inputs */
          [data-testid="stRadio"] label div,
          [data-testid="stRadio"] span {
            color: var(--giraffe-ink);
          }
          [data-testid="stFileUploader"] label,
          .stTextInput label,
          .stNumberInput label,
          .stSelectbox label,
          .stTextArea label,
          .stMarkdown p,
          .stCaption,
          label {
            color: var(--giraffe-ink);
          }

          .stTextInput > div > div > input,
          .stNumberInput > div > div > input,
          .stTextArea > div > textarea,
          .stSelectbox > div > div {
            background: var(--giraffe-surface);
            border: 1px solid var(--giraffe-border);
            border-radius: 10px;
            color: var(--giraffe-ink);
            box-shadow: 0 1px 0 var(--giraffe-shadow);
          }
          .stTextInput > div > div > input:focus {
            border-color: var(--giraffe-accent) !important;
            box-shadow: 0 0 0 3px rgba(197, 106, 40, 0.2) !important;
            outline: none !important;
          }
          .stTextInput input::placeholder,
          .stTextArea textarea::placeholder {
            color: var(--giraffe-muted);
            opacity: 1;
          }
          .stSelectbox [data-baseweb="select"] > div {
            color: var(--giraffe-ink);
          }
          .stSelectbox [data-baseweb="select"] svg {
            fill: var(--giraffe-ink);
          }
          .stNumberInput [data-baseweb="input"] {
            background: var(--giraffe-surface) !important;
            border: 1px solid var(--giraffe-border) !important;
            border-radius: 10px !important;
          }
          .stNumberInput [data-baseweb="input"] input {
            background: var(--giraffe-surface) !important;
            color: var(--giraffe-ink) !important;
          }
          .stNumberInput [data-baseweb="button"] {
            background: var(--giraffe-mist) !important;
            color: var(--giraffe-ink) !important;
            border: 1px solid var(--giraffe-border) !important;
          }
          .stNumberInput [data-baseweb="button"] svg {
            fill: var(--giraffe-ink) !important;
          }

          /* Buttons */
          .stButton > button {
            background: var(--giraffe-accent);
            color: #ffffff;
            border: none;
            border-radius: 10px;
            padding: 0.6rem 1.2rem;
            font-weight: 600;
            box-shadow: 0 6px 16px rgba(184, 107, 43, 0.25);
          }
          .stButton > button:hover {
            background: var(--giraffe-accent-dark);
          }
          .stButton > button:active {
            background: var(--giraffe-accent-pressed);
          }
          .stButton > button:focus-visible {
            outline: none;
            box-shadow: 0 0 0 3px rgba(197, 106, 40, 0.45);
          }
          .stButton > button:disabled {
            background: var(--giraffe-disabled-bg);
            color: var(--giraffe-disabled-text);
            box-shadow: none;
            cursor: not-allowed;
          }

          /* File uploader */
          [data-testid="stFileUploader"] {
            margin-top: 1.5rem;
          }
          section[data-testid="stFileUploaderDropzone"] {
            background: var(--giraffe-file-bg);
            border: 1px solid var(--giraffe-file-border);
            border-radius: 12px;
            box-shadow: inset 0 0 0 1px rgba(255,255,255,0.04);
          }
          section[data-testid="stFileUploaderDropzone"] * {
            color: var(--giraffe-file-text);
          }
          section[data-testid="stFileUploaderDropzone"] svg {
            color: var(--giraffe-file-text);
            fill: var(--giraffe-file-text);
          }
          section[data-testid="stFileUploaderDropzone"] button {
            background: var(--giraffe-accent) !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 8px !important;
            font-weight: 600 !important;
            box-shadow: 0 4px 12px rgba(184, 107, 43, 0.3) !important;
            transition: background 0.15s ease !important;
          }
          section[data-testid="stFileUploaderDropzone"] button:hover {
            background: var(--giraffe-accent-dark) !important;
          }
          section[data-testid="stFileUploaderDropzone"] button:active {
            background: var(--giraffe-accent-pressed) !important;
          }

          a {
            color: var(--giraffe-link);
          }

          /* Radio — BaseWeb DOM (Streamlit 1.55.0). Fragile selector; verify on upgrade. */
          [data-testid="stRadio"] [role="radio"] {
            border-color: var(--giraffe-border) !important;
            background: var(--giraffe-surface) !important;
          }
          [data-testid="stRadio"] [role="radio"][aria-checked="true"] {
            border-color: var(--giraffe-accent) !important;
            background: var(--giraffe-accent) !important;
          }
          [data-testid="stRadio"] [role="radio"][aria-checked="false"] {
            border-color: var(--giraffe-border) !important;
            background: var(--giraffe-surface) !important;
          }
          [data-testid="stRadio"] [role="radio"]:focus-visible {
            box-shadow: 0 0 0 3px rgba(197, 106, 40, 0.35) !important;
          }

          /* Checkbox — BaseWeb DOM (Streamlit 1.55.0). Fragile selector; verify on upgrade. */
          [data-testid="stCheckbox"] [data-baseweb="checkbox"] [data-checked="true"] > div {
            background: var(--giraffe-accent) !important;
            border-color: var(--giraffe-accent) !important;
          }
          [data-testid="stCheckbox"] [data-baseweb="checkbox"] [data-checked="false"] > div {
            background: var(--giraffe-surface) !important;
            border-color: var(--giraffe-border) !important;
          }
          [data-testid="stCheckbox"] [data-baseweb="checkbox"]:focus-visible > div {
            box-shadow: 0 0 0 3px rgba(197, 106, 40, 0.35) !important;
          }

          /* Toggle — BaseWeb DOM (Streamlit 1.55.0). Fragile selector; verify on upgrade. */
          [data-baseweb="toggle"] [data-checked="true"],
          [data-baseweb="toggle"] [data-checked="false"] {
            background: transparent !important;
          }

          /* Slider — BaseWeb DOM (Streamlit 1.55.0). Fragile selector; verify on upgrade. */
          [data-testid="stSlider"] [data-baseweb="slider"] [role="progressbar"] > div {
            background: var(--giraffe-accent) !important;
          }
          [data-testid="stSlider"] [data-baseweb="slider"] [role="slider"] {
            background: var(--giraffe-accent) !important;
            border-color: var(--giraffe-accent) !important;
            box-shadow: 0 0 0 4px rgba(197, 106, 40, 0.25) !important;
          }
          [data-testid="stSlider"] [data-baseweb="slider"] [role="slider"]:focus {
            box-shadow: 0 0 0 5px rgba(197, 106, 40, 0.4) !important;
          }
          [data-testid="stSlider"] [data-baseweb="slider"] [data-testid="stSliderTrack"] {
            background: var(--giraffe-border) !important;
          }

          /* Progress */
          [data-testid="stProgress"] > div {
            background: var(--giraffe-border) !important;
            border-radius: 99px !important;
          }
          [data-testid="stProgress"] > div > div {
            background: var(--giraffe-accent) !important;
            border-radius: 99px !important;
          }

          /* Expander */
          [data-testid="stExpander"] {
            border: 1px solid var(--giraffe-border) !important;
            border-radius: 12px !important;
            background: var(--giraffe-surface) !important;
          }
          [data-testid="stExpander"] summary {
            border-radius: 10px !important;
            background: var(--giraffe-surface) !important;
            color: var(--giraffe-ink) !important;
          }
          [data-testid="stExpander"] summary:hover {
            background: var(--giraffe-sand) !important;
          }
          [data-testid="stExpander"] summary svg {
            fill: var(--giraffe-accent) !important;
            color: var(--giraffe-accent) !important;
          }

          /* Alerts / notifications */
          .stAlert,
          div[data-testid="stNotification"],
          div[class*="stAlert"] {
            border-radius: 12px !important;
            border: 1px solid var(--giraffe-alert-border);
            background: var(--giraffe-alert-bg);
            color: var(--giraffe-ink);
          }
          div[data-testid="stNotification"][kind="info"],
          .stAlert[data-baseweb="notification"][kind="info"] {
            background: color-mix(in srgb, var(--giraffe-accent) 12%, var(--giraffe-cream)) !important;
            border-left: 3px solid var(--giraffe-accent) !important;
            color: var(--giraffe-ink) !important;
          }
          div[data-testid="stNotification"][kind="success"],
          .stAlert[data-baseweb="notification"][kind="success"] {
            background: color-mix(in srgb, #4a8c62 12%, var(--giraffe-cream)) !important;
            border-left: 3px solid #4a8c62 !important;
            color: var(--giraffe-ink) !important;
          }
          div[data-testid="stNotification"][kind="warning"],
          .stAlert[data-baseweb="notification"][kind="warning"] {
            background: color-mix(in srgb, #c9a227 12%, var(--giraffe-cream)) !important;
            border-left: 3px solid #c9a227 !important;
            color: var(--giraffe-ink) !important;
          }
          div[data-testid="stNotification"][kind="error"],
          .stAlert[data-baseweb="notification"][kind="error"] {
            background: color-mix(in srgb, #c0392b 12%, var(--giraffe-cream)) !important;
            border-left: 3px solid #c0392b !important;
            color: var(--giraffe-ink) !important;
          }

          /* Dataframe / tables */
          .stDataFrame, [data-testid="stTable"] {
            border: 1px solid var(--giraffe-border);
            background: var(--giraffe-surface);
          }
          .stDataFrame table, [data-testid="stTable"] table {
            background: var(--giraffe-surface);
            color: var(--giraffe-ink);
          }
          .stDataFrame th, .stDataFrame td,
          [data-testid="stTable"] th, [data-testid="stTable"] td {
            border-bottom: 1px solid var(--giraffe-sand);
          }
          .stDataFrame thead th, [data-testid="stTable"] thead th {
            background: var(--giraffe-mist);
            color: var(--giraffe-ink);
          }

          /* Scrollbar */
          ::-webkit-scrollbar {
            width: 7px;
            height: 7px;
          }
          ::-webkit-scrollbar-track {
            background: var(--giraffe-cream);
            border-radius: 99px;
          }
          ::-webkit-scrollbar-thumb {
            background: var(--giraffe-border);
            border-radius: 99px;
          }
          ::-webkit-scrollbar-thumb:hover {
            background: var(--giraffe-tan);
          }

          /* Status widget */
          [data-testid="stStatusWidget"],
          [data-testid="stStatusWidget"] * {
            color: var(--giraffe-ink) !important;
          }
          [data-testid="stStatusWidget"] [data-testid="stExpanderToggleIcon"] {
            color: var(--giraffe-accent) !important;
          }
          [data-testid="stStatusWidget"] > div:first-child {
            border-color: var(--giraffe-accent) !important;
            background: color-mix(in srgb, var(--giraffe-accent) 10%, var(--giraffe-surface)) !important;
          }
        </style>
"""
    ).safe_substitute(theme_vars)
    st.markdown(css, unsafe_allow_html=True)
    RUN_STATE_FILE = Path(tempfile.gettempdir()) / "girafon_run_state.json"

    st.markdown('<div class="eyebrow">🦒 Girafon</div>', unsafe_allow_html=True)
    st.title("Girafon — ESRS Gap Detector")
    st.write("Upload an ESG report and get ESRS disclosure gaps with evidence.")

    def _run_state():
        return st.session_state.run_state

    def _persist_run_state(rs: dict) -> None:
        rs["updated_at"] = time.time()
        try:
            RUN_STATE_FILE.write_text(json.dumps(rs), encoding="utf-8")
        except Exception:
            pass

    def _load_persisted_state() -> dict | None:
        try:
            if RUN_STATE_FILE.exists():
                raw = RUN_STATE_FILE.read_text(encoding="utf-8")
                return json.loads(raw)
        except Exception:
            return None
        return None

    def _start_run(mode_label: str, llm_cfg: LLMConfig | None = None, meta: dict | None = None) -> None:
        rs = _run_state()
        llm_provider = llm_cfg.provider if llm_cfg else ""
        llm_model = llm_cfg.model if llm_cfg else ""
        llm_location = "local" if llm_provider == "ollama" else "cloud"
        rs.update(
            {
                "running": True,
                "mode": mode_label,
                "logs": [],
                "progress": 0.0,
                "last_msg": "Starting...",
                "status": "running",
                "error": "",
                "started_at": time.strftime("%H:%M:%S"),
                "finished_at": "",
                "run_id": str(int(time.time())),
                "llm_provider": llm_provider,
                "llm_model": llm_model,
                "llm_location": llm_location,
                "meta": meta or {},
            }
        )
        header = f"Run started {time.strftime('%Y-%m-%d %H:%M:%S')} · {mode_label}"
        if llm_provider:
            header += f" · LLM: {llm_provider}/{llm_model} ({llm_location})"
        rs["logs"].append(header)
        rs["last_msg"] = header
        _persist_run_state(rs)

    def _finish_run(status: str, error: str = "") -> None:
        rs = _run_state()
        rs.update(
            {
                "running": False,
                "status": status,
                "error": error,
                "finished_at": time.strftime("%H:%M:%S"),
                "progress": 1.0,
            }
        )
        _persist_run_state(rs)

    def _render_run_banner() -> None:
        rs = _run_state()
        if rs["running"]:
            with st.status("Running analysis...", expanded=False):
                st.write("This run is still in progress. Logs below update in real time.")
            st.info(
                f"Run in progress: {rs['mode']} - {rs['last_msg'] or 'Working...'}"
            )
            st.progress(rs.get("progress", 0.0), text=rs.get("last_msg", "Working..."))
            last_update = rs.get("updated_at", 0.0) or 0.0
            if last_update:
                stale = max(0, int(time.time() - last_update))
                if stale > 60:
                    st.warning(f"No log update for {stale}s. The run may have stalled.")
            with st.expander("Live logs", expanded=False):
                st.code("\n".join(rs["logs"][-200:]) or "Waiting for logs...")
        elif rs["status"] in ("done", "failed"):
            if rs["status"] == "done":
                st.success(
                    f"Last run finished ({rs['mode']}) at {rs.get('finished_at', '')}."
                )
            else:
                st.error(
                    f"Last run failed ({rs['mode']}) at {rs.get('finished_at', '')}. {rs.get('error', '')}"
                )

    @st.cache_data(ttl=5, show_spinner=False)
    def _fetch_ollama_models(base: str) -> list[str]:
        try:
            with urlrequest.urlopen(f"{base}/api/tags", timeout=1.5) as resp:
                data = json.load(resp)
            models = [m.get("name") for m in data.get("models", []) if m.get("name")]
            return sorted(models)
        except Exception:
            return []

    @st.cache_data(ttl=5, show_spinner=False)
    def _cached_llm_status(provider_val: str, model_val: str, ollama_host: str) -> dict:
        _ = ollama_host  # cache key binding
        status_config = LLMConfig(provider=provider_val or None, model=model_val or None)
        return get_llm_status(status_config)

    persisted = _load_persisted_state()
    if persisted and persisted.get("updated_at", 0) > _run_state().get("updated_at", 0):
        st.session_state.run_state.update(persisted)

    _render_run_banner()

    with st.sidebar:
        st.header("Controls")
        theme_label = "☀️ Light mode" if is_dark else "🌙 Dark mode"
        theme_toggle = st.toggle(theme_label, value=is_dark, key="theme_toggle")
        st.session_state.theme_mode = "dark" if theme_toggle else "light"

        with st.expander("Settings", expanded=True):
            mode_options = ["original", "omnibus"]
            mode_labels = {
                "original": "Original (ESRS Set 1 - Delegated Act 2023)",
                "omnibus": "Simplified / Omnibus (draft proposals)",
            }
            mode = st.selectbox(
                "ESRS mode",
                mode_options,
                index=0,
                format_func=lambda v: mode_labels.get(v, v),
                help="Choose the regulatory baseline used to interpret mandatory disclosures.",
            )
            schema_options = ["basic", "ig3-core", "ig3"]
            schema_labels = {
                "basic": "Basic (Girafon 20-key disclosures)",
                "ig3-core": "IG3-core (ESRS 2 + E1 + G1)",
                "ig3": "IG3 full (EFRAG guidance)",
            }
            schema_profile = st.selectbox(
                "Schema profile",
                schema_options,
                index=0,
                format_func=lambda v: schema_labels.get(v, v),
                help="Controls the depth of the scan. IG3 is guidance, not a legal standard.",
            )
            with st.expander("What do these mean?", expanded=False):
                st.markdown(
                    "**Original** - ESRS Set 1 as adopted in 2023 (Delegated Act). "
                    "This is the current legal baseline."
                )
                st.markdown(
                    "**Simplified / Omnibus** - Draft proposals to simplify ESRS. "
                    "Use for scenario analysis; requirements may still change."
                )
                st.markdown(
                    "**Basic** - 20 disclosures for a first-pass gap check."
                )
                st.markdown(
                    "**IG3-core** - ESRS 2 + E1 + G1 for a smaller, focused run."
                )
                st.markdown(
                    "**IG3 full** - EFRAG Implementation Guidance list of datapoints (non-authoritative)."
                )

            if mode == "omnibus":
                st.warning("Omnibus is draft / proposed. Treat outputs as scenario analysis.")

            provider_default = os.getenv("LLM_PROVIDER", "").strip()
            model_default = os.getenv("LLM_MODEL", "").strip()
            st.caption("LLM auto-detects (Ollama if running). Advanced override below.")
            override_llm = st.checkbox("Override provider/model (advanced)", value=False)
            if override_llm:
                provider = st.text_input(
                    "LLM provider",
                    value=provider_default,
                    placeholder="ollama, anthropic, openai",
                )
                model = st.text_input(
                    "LLM model",
                    value=model_default,
                    placeholder="llama3.2, claude-3.5-sonnet",
                )
                if provider.strip().lower() == "ollama":
                    base = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
                    models = _fetch_ollama_models(base)
                    if models:
                        try:
                            default_idx = models.index(model) if model in models else 0
                        except ValueError:
                            default_idx = 0
                        selected = st.selectbox("Ollama model (local)", models, index=default_idx)
                        if selected:
                            model = selected
                        st.caption("Selecting a model only sets the name; Ollama loads it on first call.")
                    else:
                        st.caption(f"Ollama models not detected (check OLLAMA_HOST: {base}).")
            else:
                provider = ""
                model = ""

            llm_status = None
            llm_status_msg = ""
            try:
                llm_status = _cached_llm_status(
                    provider or "",
                    model or "",
                    os.getenv("OLLAMA_HOST", "http://localhost:11434"),
                )
            except Exception as e:
                llm_status = {
                    "state": "error",
                    "detail": str(e),
                    "provider": provider or "auto",
                    "model": model or "auto",
                }

            if llm_status:
                provider_label = llm_status.get("provider", "auto")
                model_label = llm_status.get("model", "")
                state = llm_status.get("state", "unknown")
                detail = llm_status.get("detail", "")
                header = f"LLM: {provider_label} {('/ ' + model_label) if model_label else ''}".strip()

                if state == "connected":
                    st.success(f"{header} - connected. {detail}")
                elif state == "configured":
                    st.info(f"{header} - API key set (connectivity not verified). {detail}")
                elif state == "unreachable":
                    st.error(f"{header} - not reachable. {detail}")
                    st.caption("LLM calls will fall back to keyword-only detection.")
                elif state == "missing_key":
                    st.error(f"{header} - missing API key. {detail}")
                elif state == "error":
                    st.error(f"LLM status error: {detail}")
                else:
                    st.warning(f"{header} - {state}. {detail}")

            concurrent = st.slider("Max concurrent calls", min_value=1, max_value=20, value=1, step=1)

            st.subheader("Chunking")
            auto_tune = st.toggle(
                "Auto-tune speed vs reliability",
                value=st.session_state.auto_tune_chunking,
                key="auto_tune_chunking",
            )
            speed_reliability = st.slider(
                "Speed ←→ Reliability",
                min_value=0,
                max_value=100,
                value=st.session_state.speed_reliability,
                step=5,
                key="speed_reliability",
                disabled=not auto_tune,
                help="Left = faster runs, right = higher recall with more overlap.",
            )
            if auto_tune:
                tuned_chunk, tuned_overlap, tuned_min = _auto_chunk_settings(speed_reliability)
                st.session_state.chunk_words = tuned_chunk
                st.session_state.overlap_words = tuned_overlap
                st.session_state.min_chunk_words = tuned_min
                st.caption(
                    f"Auto-tuned: chunk {tuned_chunk} · overlap {tuned_overlap} · min {tuned_min}"
                )

            chunk_words = st.slider(
                "Chunk words",
                min_value=100,
                max_value=1500,
                value=st.session_state.chunk_words,
                step=50,
                key="chunk_words",
                disabled=auto_tune,
            )
            overlap_words = st.slider(
                "Overlap words",
                min_value=0,
                max_value=500,
                value=st.session_state.overlap_words,
                step=25,
                key="overlap_words",
                disabled=auto_tune,
            )
            min_chunk_words = st.slider(
                "Min chunk words",
                min_value=10,
                max_value=300,
                value=st.session_state.min_chunk_words,
                step=5,
                key="min_chunk_words",
                disabled=auto_tune,
            )

    mode_ui = st.radio(
        "Mode",
        ["Single report", "Compare two reports", "Batch analysis"],
        horizontal=True,
        disabled=_run_state()["running"],
    )

    def _init_log_panel(title: str = "Process log"):
        st.subheader(title)
        rs = _run_state()
        progress_bar = st.progress(rs.get("progress", 0.0), text=rs.get("last_msg") or "Waiting to start...")
        log_box = st.empty()
        log_box.code("\n".join(rs.get("logs", [])[-200:]) or "Waiting to start...")

        def _update_progress(msg: str) -> None:
            if msg.startswith("Step 1/4"):
                rs["progress"] = 0.2
            elif msg.startswith("Step 2/4"):
                rs["progress"] = 0.45
            elif msg.startswith("Step 2b/4"):
                rs["progress"] = 0.55
            elif msg.startswith("Step 3/4"):
                rs["progress"] = 0.75
            elif msg.startswith("Step 4/4"):
                rs["progress"] = 0.9
            progress_bar.progress(rs["progress"], text=msg)

        def _log(msg: str) -> None:
            if rs["logs"] and rs["logs"][-1] == msg:
                return
            rs["logs"].append(msg)
            if len(rs["logs"]) > 500:
                rs["logs"] = rs["logs"][-500:]
            rs["last_msg"] = msg
            log_box.code("\n".join(rs["logs"][-200:]))
            _update_progress(msg)
            _persist_run_state(rs)
            try:
                print(msg, flush=True)
            except Exception:
                pass

        return rs["logs"], _log, log_box, progress_bar

    if mode_ui == "Compare two reports":
        if _run_state()["running"] and _run_state()["mode"] == "Compare two reports":
            meta = _run_state().get("meta", {})
            st.info("Comparison run in progress. Inputs are hidden while the run completes.")
            if meta:
                st.write(f"Baseline: {meta.get('baseline','')} · Comparison: {meta.get('comparison','')}")
            _init_log_panel("Comparison log")
            return
        with st.expander("Comparison inputs", expanded=not _run_state()["running"]):
            col_a, col_b = st.columns(2)
            with col_a:
                uploaded_base = st.file_uploader("Baseline report (.pdf or .html)", type=["pdf", "html", "htm"], key="base_upload")
                base_label = st.text_input("Baseline label", value="Baseline", key="base_label")
            with col_b:
                uploaded_new = st.file_uploader("Comparison report (.pdf or .html)", type=["pdf", "html", "htm"], key="new_upload")
                new_label = st.text_input("Comparison label", value="Comparison", key="new_label")

            run_compare = st.button(
                "Run comparison",
                type="primary",
                disabled=not (uploaded_base and uploaded_new) or _run_state()["running"],
            )

        if not run_compare:
            return

        if schema_profile == "ig3":
            st.warning("IG3 is EFRAG guidance (non-authoritative) and can take a long time.")
        if schema_profile == "ig3-core":
            st.info("IG3-core is ESRS 2 + E1 + G1 for a smaller, focused run.")

        try:
            llm_config = LLMConfig(provider=provider or None, model=model or None)
        except ValueError as e:
            st.error(f"LLM configuration error: {e}")
            return

        _start_run(
            "Compare two reports",
            llm_cfg=llm_config,
            meta={"baseline": uploaded_base.name, "comparison": uploaded_new.name},
        )
        max_concurrent = int(concurrent)
        _, _log, _, progress_bar = _init_log_panel("Comparison log")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            base_path = tmpdir_path / uploaded_base.name
            new_path = tmpdir_path / uploaded_new.name
            base_path.write_bytes(uploaded_base.getbuffer())
            new_path.write_bytes(uploaded_new.getbuffer())

            stamp = time.strftime("%Y%m%d_%H%M%S")
            out_root = _output_root()
            base_html_path = out_root / f"{base_path.stem}_report_{stamp}.html"
            new_html_path = out_root / f"{new_path.stem}_report_{stamp}.html"
            diff_html_path = out_root / f"{base_path.stem}_vs_{new_path.stem}_diff_{stamp}.html"
            _log(f"Output folder: {out_root}")

            with st.status("Running comparison...", expanded=True) as status:
                try:
                    frameworks_dir = Path(__file__).parent / "esg_analyzer" / "frameworks"
                    schema_path = frameworks_dir / ("esrs_schema.json" if schema_profile == "basic" else "esrs_ig3_schema.json")
                    taxonomy_map_path = None
                    if schema_profile == "basic":
                        taxonomy_map_path = frameworks_dir / "esrs_taxonomy_map.json"
                        if not taxonomy_map_path.exists():
                            taxonomy_map_path = None
                    ig3_scope = None
                    if schema_profile == "ig3-core":
                        ig3_scope = {"ESRS 2", "ESRS 2 MDR", "E1", "G1"}

                    base_result = run_pipeline(
                        doc_path=base_path,
                        company_name=base_label or clean_company_name(base_path.name),
                        mode=mode,
                        llm_config=llm_config,
                        schema_path=schema_path,
                        taxonomy_map_path=taxonomy_map_path,
                        ig3_scope=ig3_scope,
                        schema_profile=schema_profile,
                        output_path=str(base_html_path),
                        chunk_words=int(chunk_words),
                        overlap_words=int(overlap_words),
                        min_chunk_words=int(min_chunk_words),
                        max_concurrent=max_concurrent,
                        progress=_log,
                        warn=_log,
                    )
                    new_result = run_pipeline(
                        doc_path=new_path,
                        company_name=new_label or clean_company_name(new_path.name),
                        mode=mode,
                        llm_config=llm_config,
                        schema_path=schema_path,
                        taxonomy_map_path=taxonomy_map_path,
                        ig3_scope=ig3_scope,
                        schema_profile=schema_profile,
                        output_path=str(new_html_path),
                        chunk_words=int(chunk_words),
                        overlap_words=int(overlap_words),
                        min_chunk_words=int(min_chunk_words),
                        max_concurrent=max_concurrent,
                        progress=_log,
                        warn=_log,
                    )
                except Exception as e:
                    status.update(label="Failed", state="error")
                    progress_bar.progress(1.0, text="Failed")
                    _finish_run("failed", str(e))
                    st.error(f"Comparison failed: {e}")
                    st.exception(e)
                    return

                status.update(label="Done", state="complete")
                progress_bar.progress(1.0, text="Done")
                _finish_run("done")

            diff_report = compute_diff_report(
                base_result.score_report,
                new_result.score_report,
                base_label=base_label or "Baseline",
                new_label=new_label or "Comparison",
                base_report_html=Path(base_result.output_path).name,
                new_report_html=Path(new_result.output_path).name,
            )
            diff_html = generate_diff_report(diff_report, output_path=str(diff_html_path))

            st.success("Diff report generated.")
            st.caption(f"Saved to: {diff_html_path}")

            base_score = base_result.score_report.get("overall_score")
            new_score = new_result.score_report.get("overall_score")
            base_comp = base_result.score_report.get("compliance_rate")
            new_comp = new_result.score_report.get("compliance_rate")

            col1, col2, col3 = st.columns(3)
            col1.metric("Overall score", f"{new_score}/100", delta=f"{(new_score or 0) - (base_score or 0):.1f}")
            col2.metric("Compliance rate", f"{new_comp}%", delta=f"{(new_comp or 0) - (base_comp or 0):.1f}")
            col3.metric("Improved / Regressed", f"{diff_report['counts']['improved']} / {diff_report['counts']['regressed']}")

            preview_rows = [
                {
                    "Section": item["section"],
                    "Disclosure": item["name"],
                    "Before": item["base_status"],
                    "After": item["new_status"],
                    "Change": item["transition"],
                    "Mandatory": item["mandatory"],
                }
                for item in diff_report["items"]
            ]
            st.subheader("Disclosure changes")
            st.dataframe(preview_rows, width="stretch", height=420)

            st.download_button(
                "Download diff HTML report",
                data=diff_html,
                file_name=Path(diff_html_path).name,
                mime="text/html",
            )
            try:
                st.download_button(
                    "Download baseline report",
                    data=Path(base_result.output_path).read_text(encoding="utf-8"),
                    file_name=Path(base_result.output_path).name,
                    mime="text/html",
                )
                st.download_button(
                    "Download comparison report",
                    data=Path(new_result.output_path).read_text(encoding="utf-8"),
                    file_name=Path(new_result.output_path).name,
                    mime="text/html",
                )
            except OSError as e:
                st.warning(f"Could not load one of the HTML reports: {e}")

            st.components.v1.html(diff_html, height=800, scrolling=True)
        return

    if mode_ui == "Batch analysis":
        if _run_state()["running"] and _run_state()["mode"] == "Batch analysis":
            meta = _run_state().get("meta", {})
            st.info("Batch run in progress. Inputs are hidden while the run completes.")
            if meta:
                st.write(f"Files: {meta.get('count','')}")
            _init_log_panel("Batch log")
            return
        uploaded_batch = st.file_uploader(
            "Upload ESG reports (.pdf)",
            type=["pdf"],
            accept_multiple_files=True,
            key="batch_upload",
        )
        run_batch = st.button(
            "Run batch analysis",
            type="primary",
            disabled=not uploaded_batch or _run_state()["running"],
        )

        if not run_batch:
            return

        batch_provider = "ollama"
        batch_model = "qwen2.5:14b"
        if provider != batch_provider or model != batch_model:
            st.info("Batch mode forces Ollama / qwen2.5:14b to avoid API rate limits. Make sure Ollama is running.")
        provider = batch_provider
        model = batch_model

        if schema_profile == "ig3":
            st.warning("IG3 is EFRAG guidance (non-authoritative) and can take a long time.")
        if schema_profile == "ig3-core":
            st.info("IG3-core is ESRS 2 + E1 + G1 for a smaller, focused run.")

        try:
            llm_config = LLMConfig(provider=provider or None, model=model or None)
        except ValueError as e:
            st.error(f"LLM configuration error: {e}")
            return

        _start_run(
            "Batch analysis",
            llm_cfg=llm_config,
            meta={"count": len(uploaded_batch)},
        )
        max_concurrent = int(concurrent)
        _, _log, _, progress_bar = _init_log_panel("Batch log")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            input_dir = tmpdir_path / "input"
            stamp = time.strftime("%Y%m%d_%H%M%S")
            output_dir = _output_root() / f"batch_{stamp}"
            input_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            for f in uploaded_batch:
                (input_dir / f.name).write_bytes(f.getbuffer())

            with st.status("Running batch analysis...", expanded=True) as status:
                try:
                    frameworks_dir = Path(__file__).parent / "esg_analyzer" / "frameworks"
                    schema_path = frameworks_dir / ("esrs_schema.json" if schema_profile == "basic" else "esrs_ig3_schema.json")
                    taxonomy_map_path = None
                    if schema_profile == "basic":
                        taxonomy_map_path = frameworks_dir / "esrs_taxonomy_map.json"
                        if not taxonomy_map_path.exists():
                            taxonomy_map_path = None
                    ig3_scope = None
                    if schema_profile == "ig3-core":
                        ig3_scope = {"ESRS 2", "ESRS 2 MDR", "E1", "G1"}

                    summary = analyze_batch(
                        input_dir=input_dir,
                        output_dir=output_dir,
                        llm_config=llm_config,
                        schema_path=schema_path,
                        taxonomy_map_path=taxonomy_map_path,
                        ig3_scope=ig3_scope,
                        schema_profile=schema_profile,
                        mode=mode,
                        chunk_words=int(chunk_words),
                        overlap_words=int(overlap_words),
                        min_chunk_words=int(min_chunk_words),
                        max_concurrent=max_concurrent,
                        progress=_log,
                        warn=_log,
                    )
                except Exception as e:
                    status.update(label="Failed", state="error")
                    progress_bar.progress(1.0, text="Failed")
                    _finish_run("failed", str(e))
                    st.error(f"Batch analysis failed: {e}")
                    st.exception(e)
                    return

                status.update(label="Done", state="complete")
                progress_bar.progress(1.0, text="Done")
                _finish_run("done")
            _log(f"Batch output folder: {output_dir}")

            try:
                summary_json = (output_dir / "summary.json").read_text(encoding="utf-8")
            except OSError as e:
                st.error(f"Could not read summary.json: {e}")
                return
            try:
                comparison_html = (output_dir / "comparison.html").read_text(encoding="utf-8")
            except OSError as e:
                st.error(f"Could not read comparison.html: {e}")
                return

            report_blobs = []
            for item in summary:
                report_name = item.get("report_html")
                if not report_name:
                    continue
                report_path = output_dir / report_name
                if not report_path.exists():
                    continue
                report_blobs.append(
                    {
                        "name": report_name,
                        "company": item.get("company", report_name),
                        "html": report_path.read_text(encoding="utf-8"),
                    }
                )

            st.success("Batch analysis complete.")

            st.subheader("Summary")
            st.dataframe(summary, width="stretch", height=320)

            st.download_button(
                "Download summary.json",
                data=summary_json,
                file_name="summary.json",
                mime="application/json",
            )
            st.download_button(
                "Download comparison.html",
                data=comparison_html,
                file_name="comparison.html",
                mime="text/html",
            )

            with st.expander("Individual reports"):
                for blob in report_blobs:
                    st.download_button(
                        f"Download {blob['company']}",
                        data=blob["html"],
                        file_name=blob["name"],
                        mime="text/html",
                    )

            st.subheader("Comparison workspace")
            st.components.v1.html(comparison_html, height=800, scrolling=True)
            st.caption(f"Saved to: {output_dir}")

        return

    uploaded = st.file_uploader("Upload ESG report (.pdf or .html)", type=["pdf", "html", "htm"])
    company_name = st.text_input("Company name (optional)", value="")
    run = st.button(
        "Run analysis",
        type="primary",
        disabled=uploaded is None or _run_state()["running"],
    )

    if _run_state()["running"] and _run_state()["mode"] == "Single report":
        meta = _run_state().get("meta", {})
        st.info("Single report run in progress. Inputs are hidden while the run completes.")
        if meta:
            st.write(f"File: {meta.get('file','')}")
        _init_log_panel("Analysis log")
        return

    def _render_history() -> None:
        if not st.session_state.report_history:
            return
        st.subheader("Report history")
        options = {r["id"]: r["title"] for r in st.session_state.report_history}
        selected_id = st.selectbox(
            "Select a report to view",
            options=list(options.keys())[::-1],
            format_func=lambda rid: options[rid],
            key="history_select",
        )
        st.session_state.selected_report_id = selected_id
        selected = next((r for r in st.session_state.report_history if r["id"] == selected_id), None)
        if selected:
            st.download_button(
                "Download HTML report",
                data=selected["html"],
                file_name=f"{selected['id']}.html",
                mime="text/html",
            )
            if selected.get("path"):
                st.caption(f"Saved to: {selected['path']}")
            st.components.v1.html(selected["html"], height=800, scrolling=True)

    if not run:
        _render_history()
        return

    if schema_profile == "ig3":
        st.warning("IG3 is EFRAG guidance (non-authoritative) and can take a long time.")
    if schema_profile == "ig3-core":
        st.info("IG3-core is ESRS 2 + E1 + G1 for a smaller, focused run.")

    if uploaded is None:
        st.error("Please upload a report first.")
        return

    try:
        llm_config = LLMConfig(provider=provider or None, model=model or None)
    except ValueError as e:
        st.error(f"LLM configuration error: {e}")
        return

    _start_run(
        "Single report",
        llm_cfg=llm_config,
        meta={"file": uploaded.name},
    )
    max_concurrent = int(concurrent)
    _, _log, _, progress_bar = _init_log_panel("Analysis log")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        input_path = tmpdir_path / uploaded.name
        input_path.write_bytes(uploaded.getbuffer())
        stamp = time.strftime("%Y%m%d_%H%M%S")
        output_path = _output_root() / f"{input_path.stem}_report_{stamp}.html"
        _log(f"Output folder: {output_path.parent}")

        with st.status("Running analysis...", expanded=True) as status:
            try:
                frameworks_dir = Path(__file__).parent / "esg_analyzer" / "frameworks"
                schema_path = frameworks_dir / ("esrs_schema.json" if schema_profile == "basic" else "esrs_ig3_schema.json")
                taxonomy_map_path = None
                if schema_profile == "basic":
                    taxonomy_map_path = frameworks_dir / "esrs_taxonomy_map.json"
                    if not taxonomy_map_path.exists():
                        taxonomy_map_path = None
                ig3_scope = None
                if schema_profile == "ig3-core":
                    ig3_scope = {"ESRS 2", "ESRS 2 MDR", "E1", "G1"}
                result = run_pipeline(
                    doc_path=input_path,
                    company_name=company_name or clean_company_name(input_path.name),
                    mode=mode,
                    llm_config=llm_config,
                    schema_path=schema_path,
                    taxonomy_map_path=taxonomy_map_path,
                    ig3_scope=ig3_scope,
                    schema_profile=schema_profile,
                    output_path=str(output_path),
                    chunk_words=int(chunk_words),
                    overlap_words=int(overlap_words),
                    min_chunk_words=int(min_chunk_words),
                    max_concurrent=max_concurrent,
                    progress=_log,
                    warn=_log,
                )
            except Exception as e:
                status.update(label="Failed", state="error")
                progress_bar.progress(1.0, text="Failed")
                _finish_run("failed", str(e))
                st.error(f"Pipeline failed: {e}")
                st.exception(e)
                return

            status.update(label="Done", state="complete")
            progress_bar.progress(1.0, text="Done")
            _finish_run("done")

        html = Path(result.output_path).read_text(encoding="utf-8")
        st.success("Report generated.")

        report_id = f"r{int(time.time())}"
        report_title = f"{result.score_report.get('company_name', '') or (company_name or input_path.stem).title()} - {time.strftime('%Y-%m-%d %H:%M:%S')}"
        st.session_state.report_history.append({
            "id": report_id,
            "title": report_title,
            "html": html,
            "path": str(result.output_path),
            "score": result.score_report["overall_score"],
            "compliance": result.score_report["compliance_rate"],
            "found": result.score_report["found_count"],
            "partial": result.score_report["partial_count"],
            "missing": result.score_report["missing_count"],
        })
        st.session_state.selected_report_id = report_id

        col1, col2, col3 = st.columns(3)
        col1.metric("Overall score", f"{result.score_report['overall_score']}/100")
        col2.metric("Compliance rate", f"{result.score_report['compliance_rate']}%")
        col3.metric(
            "Found / Partial / Missing",
            f"{result.score_report['found_count']} / {result.score_report['partial_count']} / {result.score_report['missing_count']}",
        )

        _render_history()


if __name__ == "__main__":
    main()
