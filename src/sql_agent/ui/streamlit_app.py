from __future__ import annotations

import pandas as pd
import streamlit as st

from sql_agent.config import get_settings
from sql_agent.core.bootstrap import ensure_demo_database
from sql_agent.core.errors import (
    ExecutionError,
    GuardrailError,
    LLMError,
    RateLimitError,
    SchemaError,
)
from sql_agent.core.observability import configure_logging
from sql_agent.core.pipeline import AnalysisResult, NL2SQLAgent
from sql_agent.ui.connections import (
    ActiveConnection,
    close_connection,
    connect_to_database_url,
    connect_to_uploaded_sqlite,
)


def _install_styles() -> None:
    st.markdown(
        """
        <style>
        :root { --surface: #101113; --panel: #151719; --line: #25282b; }
        .stApp { background: #0b0c0e; }
        [data-testid="stSidebar"] { background: #090a0b; border-right: 1px solid #202225; }
        [data-testid="stSidebar"] > div:first-child { padding-top: 1rem; }
        .block-container { max-width: 1440px; padding-top: 2rem; padding-bottom: 3rem; }
        [data-testid="stMetric"] { background: #121416; border: 1px solid #25282b; padding: 1rem; border-radius: 12px; }
        div[data-testid="stVerticalBlockBorderWrapper"] { background: #101113; border-color: #25282b; border-radius: 14px; }
        .eyebrow { color: #8b929a; text-transform: uppercase; letter-spacing: .12em; font-size: .72rem; font-weight: 700; }
        .brand-mark { display: inline-flex; width: 30px; height: 30px; align-items: center; justify-content: center; background: #2858e8; border-radius: 9px; margin-right: 9px; font-weight: 800; }
        .muted { color: #959ba3; }
        .app-footer { margin-top: 3rem; padding: 1rem 0 .25rem; border-top: 1px solid #25282b; color: #8b929a; text-align: center; font-size: .78rem; }
        .app-footer a { color: #aebdff; text-decoration: none; }
        .app-footer a:hover { text-decoration: underline; }
        button[kind="primary"] { background: #315fe8; border-color: #315fe8; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _new_demo_connection() -> ActiveConnection:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = ensure_demo_database(settings)
    try:
        agent = NL2SQLAgent(settings, engine=engine)
        return ActiveConnection(label="Sample sales database", agent=agent, engine=engine)
    except Exception:
        engine.dispose()
        raise


def _set_connection(connection: ActiveConnection) -> None:
    close_connection(st.session_state.get("active_connection"))
    st.session_state.active_connection = connection
    st.session_state.history = []
    # Apply navigation on the next rerun, before the keyed radio is instantiated.
    st.session_state.navigate_to_dashboard = True


def _connection() -> ActiveConnection:
    connection = st.session_state.get("active_connection")
    if connection is None:
        connection = _new_demo_connection()
        st.session_state.active_connection = connection
    return connection


def _show_sidebar(connection: ActiveConnection) -> str:
    with st.sidebar:
        st.markdown(
            '<div style="display:flex;align-items:center;margin:4px 0 26px">'
            '<span class="brand-mark">Q</span><b>QueryPilot</b></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="eyebrow">Workspace</div>', unsafe_allow_html=True)
        page = st.radio(
            "Navigation",
            ["Dashboard", "Connections", "Sessions"],
            key="page",
            label_visibility="collapsed",
            format_func=lambda item: {
                "Dashboard": "⌂   Dashboard",
                "Connections": "◉   Connections",
                "Sessions": "◷   Sessions",
            }[item],
        )
        st.divider()
        st.markdown('<div class="eyebrow">Active database</div>', unsafe_allow_html=True)
        st.caption(connection.label)
        st.caption(f"{connection.agent.dialect} · {len(connection.agent.schema.tables)} tables")
        st.divider()
        st.caption("Ollama · local inference")
    return page


def _render_connections() -> None:
    st.title("Connections")
    st.caption("Choose the database schema the agent should use for this session.")

    sample_tab, upload_tab, url_tab = st.tabs(
        ["Sample database", "Upload SQLite", "Database URL"]
    )
    with sample_tab:
        st.markdown("### Demo sales data")
        st.write("Explore a ready-to-use SQLite dataset with customers, orders, products, and payments.")
        if st.button("Connect sample database", type="primary", use_container_width=False):
            try:
                with st.spinner("Opening sample database..."):
                    _set_connection(_new_demo_connection())
                st.success("Connected to the sample database.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not open the sample database ({type(exc).__name__}).")

    with upload_tab:
        st.markdown("### Upload a SQLite database")
        st.caption("Supported formats: .db, .sqlite, .sqlite3. Maximum upload size: 100 MB. The file is opened read-only and kept only for this session.")
        with st.form("upload_database_form"):
            uploaded = st.file_uploader(
                "Choose a database file",
                type=["db", "sqlite", "sqlite3"],
                help="Upload a SQLite database that contains tables you want to query.",
            )
            submit_upload = st.form_submit_button("Connect uploaded database", type="primary")
        if submit_upload:
            if uploaded is None:
                st.warning("Choose a database file first.")
            else:
                try:
                    with st.spinner("Inspecting database schema..."):
                        connection = connect_to_uploaded_sqlite(
                            get_settings(), uploaded.name, uploaded.getvalue()
                        )
                        _set_connection(connection)
                    st.success(f"Connected to {connection.label}.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not connect to that file: {exc}")

    with url_tab:
        st.markdown("### Connect with a database URL")
        st.caption("Supported here: SQLite file URLs and PostgreSQL URLs. Use a read-only database account. The URL is kept in this browser session and is not shown in the interface.")
        with st.form("database_url_form"):
            database_url = st.text_input(
                "SQLAlchemy database URL",
                type="password",
                placeholder="postgresql+psycopg2://readonly_user:password@host:5432/database",
                help="The URL is masked. It is used only to create this session's connection.",
            )
            submit_url = st.form_submit_button("Connect database", type="primary")
        if submit_url:
            if not database_url.strip():
                st.warning("Enter a database URL first.")
            else:
                try:
                    with st.spinner("Connecting and inspecting schema..."):
                        connection = connect_to_database_url(get_settings(), database_url)
                        _set_connection(connection)
                    st.success(f"Connected to {connection.label}.")
                    st.rerun()
                except Exception as exc:
                    # Driver errors may include the full URL, including its password.
                    st.error(f"Could not connect ({type(exc).__name__}). Check the URL, network access, and database permissions.")


def _render_dashboard(connection: ActiveConnection) -> None:
    st.markdown('<div class="eyebrow">Local workspace</div>', unsafe_allow_html=True)
    st.title("QueryPilot")
    st.caption("Ask a question in plain language. The agent builds, guards, and runs a read-only query.")

    left, right = st.columns([0.95, 1.8], gap="large")
    with left:
        with st.container(border=True):
            st.markdown("#### Connected database")
            st.markdown(f"**{connection.label}**")
            st.caption(f"{connection.agent.dialect.title()} dialect")
            st.metric("Available tables", len(connection.agent.schema.tables))
            with st.expander("Browse schema"):
                st.code(connection.agent.schema.to_prompt_string(), language="text")
            if st.button("Change database", use_container_width=True):
                st.session_state.page = "Connections"
                st.rerun()

        with st.container(border=True):
            st.markdown("#### Example questions")
            examples = [
                "What were total payments by region?",
                "Which products have the most order items?",
                "Show the five largest orders.",
            ]
            for example in examples:
                if st.button(example, key=f"example_{example}", use_container_width=True):
                    st.session_state.question = example
                    st.rerun()

    with right:
        with st.container(border=True):
            st.markdown("#### Ask your database")
            st.caption("The question and result stay in this local app session.")
            with st.form("question_form"):
                question = st.text_area(
                    "Question",
                    key="question",
                    placeholder="For example: What were total payments by region?",
                    height=125,
                    max_chars=connection.agent.settings.max_question_chars,
                    label_visibility="collapsed",
                )
                submitted = st.form_submit_button("Run analysis", type="primary", use_container_width=True)
        if submitted:
            if not question.strip():
                st.warning("Enter a question first.")
            else:
                try:
                    with st.spinner("Generating SQL and analyzing the result..."):
                        result = connection.agent.analyze(
                            question,
                            client_key=st.session_state.get("session_id", "local-ui"),
                        )
                    _store_result(result, connection.label)
                except GuardrailError as exc:
                    st.error(f"The query was blocked: {exc}")
                except RateLimitError as exc:
                    st.warning(str(exc))
                except (ExecutionError, LLMError, SchemaError) as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Could not analyze this question ({type(exc).__name__}).")

    latest = st.session_state.get("latest_result")
    if latest:
        st.markdown("### Latest result")
        answer_col, details_col = st.columns([1.8, 1])
        with answer_col:
            with st.container(border=True):
                st.markdown("#### Answer")
                st.write(latest["answer"])
        with details_col:
            with st.container(border=True):
                st.markdown("#### Query details")
                st.caption(f"Source: {latest['source']}")
                st.caption(f"Rows: {len(latest['rows'])} · Retries: {latest['retries']}")
                st.caption(f"Request: {latest['request_id']}")
        with st.expander("View guarded SQL"):
            st.code(latest["sql"], language="sql")
        if latest["warnings"]:
            st.warning(" · ".join(latest["warnings"]))
        if latest["rows"]:
            st.dataframe(
                pd.DataFrame(latest["rows"], columns=latest["columns"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("The query returned no rows.")


def _store_result(result: AnalysisResult, source: str) -> None:
    stored = {
        "question": result.question,
        "answer": result.answer,
        "sql": result.sql,
        "columns": result.columns,
        "rows": result.rows,
        "source": source,
        "request_id": result.request_id,
        "retries": result.retries,
        "warnings": result.warnings,
    }
    st.session_state.latest_result = stored
    st.session_state.history = [stored, *st.session_state.get("history", [])]


def _render_sessions() -> None:
    st.title("Sessions")
    st.caption("Recent questions from this browser session.")
    history = st.session_state.get("history", [])
    if not history:
        st.info("Your questions will appear here after you run an analysis.")
        return
    for index, item in enumerate(history):
        with st.expander(item["question"], expanded=index == 0):
            st.caption(f"{item['source']} · {len(item['rows'])} row(s)")
            st.markdown(item["answer"])
            with st.expander("Guarded SQL"):
                st.code(item["sql"], language="sql")
            if item["rows"]:
                st.dataframe(
                    pd.DataFrame(item["rows"], columns=item["columns"]),
                    use_container_width=True,
                    hide_index=True,
                )


def main() -> None:
    st.set_page_config(
        page_title="QueryPilot | Natural Language SQL",
        page_icon="Q",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _install_styles()
    if st.session_state.pop("navigate_to_dashboard", False):
        st.session_state.page = "Dashboard"
    try:
        connection = _connection()
    except Exception as exc:
        st.error(f"Could not start the database workspace ({type(exc).__name__}).")
        st.stop()

    page = _show_sidebar(connection)
    if page == "Connections":
        _render_connections()
    elif page == "Sessions":
        _render_sessions()
    else:
        _render_dashboard(connection)
    st.markdown(
        '<div class="app-footer">Created by <strong>Prerak Khandelwal</strong>'
        ' &nbsp;·&nbsp; '
        '<a href="https://www.linkedin.com/in/prerak-khandelwal23/" '
        'target="_blank" rel="noopener noreferrer">Connect with me on LinkedIn ↗</a></div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
