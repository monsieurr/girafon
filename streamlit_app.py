from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import streamlit as st

from esg_analyzer.llm_provider import LLMConfig
from esg_analyzer.pipeline import run_pipeline


def _hydrate_env_from_secrets() -> None:
    """
    Map Streamlit secrets to environment variables for LLMConfig.
    Keeps keys out of the UI and works on Streamlit Cloud.
    """
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
        if key in secrets and not os.environ.get(key):
            os.environ[key] = str(secrets[key])


def main() -> None:
    st.set_page_config(page_title="ESRS Gap Detector", page_icon="🦒", layout="wide")
    _hydrate_env_from_secrets()
    if "report_history" not in st.session_state:
        st.session_state.report_history = []
    if "selected_report_id" not in st.session_state:
        st.session_state.selected_report_id = None
    st.markdown(
        """
        <style>
          @import url('https://api.fontshare.com/v2/css?f[]=satoshi@400,500,600,700&display=swap');
          @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');

          :root {
            --giraffe-ink: #24180f;
            --giraffe-brown: #a1581f;
            --giraffe-tan: #e0a45c;
            --giraffe-cream: #f9eee0;
            --giraffe-sand: #d5b08a;
            --giraffe-mist: #f0dbc2;
            --giraffe-accent: #cc6a1d;
            --giraffe-shadow: rgba(60, 36, 20, 0.12);
          }

          html, body, [class*="stApp"] {
            font-family: 'Inter', sans-serif;
            color: var(--giraffe-ink);
            background: var(--giraffe-cream);
          }

          h1, h2, h3, h4, h5, h6, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
            font-family: 'Satoshi', sans-serif;
            color: var(--giraffe-ink);
            letter-spacing: 0.3px;
          }

          [data-testid="stSidebar"] {
            background: #ecd4b9;
            border-right: 1px solid var(--giraffe-sand);
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

          .stTextInput > div > div > input,
          .stNumberInput > div > div > input,
          .stTextArea > div > textarea,
          .stSelectbox > div > div {
            background: #ffffff;
            border: 1px solid #b67b45;
            border-radius: 10px;
            color: var(--giraffe-ink);
            box-shadow: 0 1px 0 var(--giraffe-shadow);
          }
          .stTextInput input::placeholder,
          .stTextArea textarea::placeholder {
            color: #7a5a3f;
            opacity: 1;
          }
          .stSelectbox [data-baseweb="select"] > div {
            color: var(--giraffe-ink);
          }
          .stSelectbox [data-baseweb="select"] svg {
            fill: var(--giraffe-ink);
          }
          .stNumberInput [data-baseweb="input"] {
            background: #ffffff !important;
            border: 1px solid #b67b45 !important;
            border-radius: 10px !important;
          }
          .stNumberInput [data-baseweb="input"] input {
            background: #ffffff !important;
            color: var(--giraffe-ink) !important;
          }
          .stNumberInput [data-baseweb="button"] {
            background: #f7e5d0 !important;
            color: var(--giraffe-ink) !important;
            border: 1px solid #b67b45 !important;
          }
          .stNumberInput [data-baseweb="button"] svg {
            fill: var(--giraffe-ink) !important;
          }

          .stButton > button {
            background: var(--giraffe-accent);
            color: #ffffff;
            border: none;
            border-radius: 10px;
            padding: 0.6rem 1.2rem;
            font-weight: 600;
            box-shadow: 0 6px 16px rgba(184, 107, 43, 0.25);
          }
          .stButton > button:disabled {
            background: #cdb08c;
            color: #3b2a1f;
            box-shadow: none;
          }

          .stButton > button:hover {
            background: #9c4f20;
          }

          .stButton > button:active {
            background: #7f3f18;
          }

          .stMetric {
            background: #ffffff;
            border: 1px solid #d2b89a;
            border-radius: 12px;
            padding: 12px 16px;
            box-shadow: 0 8px 18px var(--giraffe-shadow);
          }

          .stAlert {
            border-radius: 12px;
            border: 1px solid #d4a67a;
            background: #fff2dd;
            color: var(--giraffe-ink);
          }

          code, pre, .stCodeBlock {
            font-family: 'DM Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
            background: #fffaf3;
          }

          section[data-testid="stFileUploaderDropzone"] {
            background: linear-gradient(135deg, #2b1f16, #3c2a1e);
            border: 1px solid #4b3526;
            box-shadow: inset 0 0 0 1px rgba(255,255,255,0.04);
          }
          section[data-testid="stFileUploaderDropzone"] * {
            color: #fff3e0;
          }
          section[data-testid="stFileUploaderDropzone"] svg {
            color: #fff3e0;
            fill: #fff3e0;
          }
          section[data-testid="stFileUploaderDropzone"] button {
            background: #fff3e0;
            color: #3c2a1e;
            border-radius: 8px;
            font-weight: 600;
          }
          section[data-testid="stFileUploaderDropzone"] button:hover {
            background: #ffe3c2;
          }
          a {
            color: #8b5a2b;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("ESRS Gap Detector")
    st.write("Upload an ESG report and get ESRS disclosure gaps with evidence.")

    with st.sidebar:
        st.header("Settings")
        mode = st.selectbox("ESRS mode", ["original", "omnibus"], index=0)
        schema_profile = st.selectbox("Schema profile", ["basic", "ig3-core", "ig3"], index=0)

        provider_default = os.getenv("LLM_PROVIDER", "").strip()
        model_default = os.getenv("LLM_MODEL", "").strip()
        st.caption("LLM auto-detects (Ollama if running). Advanced override below.")
        override_llm = st.checkbox("Override provider/model (advanced)", value=False)
        if override_llm:
            provider = st.text_input("LLM provider", value=provider_default, placeholder="ollama, anthropic, openai")
            model = st.text_input("LLM model", value=model_default, placeholder="llama3.2, claude-3.5-sonnet")
        else:
            provider = ""
            model = ""

        concurrent = st.number_input("Max concurrent calls", min_value=1, max_value=20, value=1, step=1)

        st.subheader("Chunking")
        chunk_words = st.number_input("Chunk words", min_value=100, max_value=1500, value=500, step=50)
        overlap_words = st.number_input("Overlap words", min_value=0, max_value=500, value=120, step=25)
        min_chunk_words = st.number_input("Min chunk words", min_value=10, max_value=300, value=40, step=5)

    uploaded = st.file_uploader("Upload ESG report (.pdf or .html)", type=["pdf", "html", "htm"])
    company_name = st.text_input("Company name (optional)", value="")
    run = st.button("Run analysis", type="primary", disabled=uploaded is None)

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
            st.components.v1.html(selected["html"], height=800, scrolling=True)

    if not run:
        _render_history()
        return

    if schema_profile == "ig3":
        st.warning("IG3 runs 1,000+ datapoints and can take a long time.")
    if schema_profile == "ig3-core":
        st.info("IG3-core runs ESRS 2 + E1 + G1 for a fast, high-impact scan.")

    if uploaded is None:
        st.error("Please upload a report first.")
        return

    try:
        llm_config = LLMConfig(provider=provider or None, model=model or None)
    except ValueError as e:
        st.error(f"LLM configuration error: {e}")
        return

    max_concurrent = int(concurrent)

    logs: list[str] = []
    log_box = st.empty()

    def _log(msg: str) -> None:
        logs.append(msg)
        log_box.code("\n".join(logs))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        input_path = tmpdir_path / uploaded.name
        input_path.write_bytes(uploaded.getbuffer())
        output_path = tmpdir_path / f"{input_path.stem}_report.html"

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
                    company_name=company_name or input_path.stem.replace("_", " ").title(),
                    mode=mode,
                    llm_config=llm_config,
                    schema_path=schema_path,
                    taxonomy_map_path=taxonomy_map_path,
                    ig3_scope=ig3_scope,
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
                st.error(f"Pipeline failed: {e}")
                return

            status.update(label="Done", state="complete")

        html = Path(result.output_path).read_text(encoding="utf-8")
        st.success("Report generated.")

        report_id = f"r{int(time.time())}"
        report_title = f"{result.score_report.get('company_name', '') or (company_name or input_path.stem).title()} — {time.strftime('%Y-%m-%d %H:%M:%S')}"
        st.session_state.report_history.append({
            "id": report_id,
            "title": report_title,
            "html": html,
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
