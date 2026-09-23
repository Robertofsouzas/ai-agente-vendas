"""
app.py
Interface Streamlit do Agente de IA para Análise de Vendas.
Rodar com: streamlit run app.py
"""

import streamlit as st
from agent_core import run_agent, ALLOWED_TABLES

st.set_page_config(
    page_title="Agente de Vendas",
    page_icon="📊",
    layout="wide",
)

EXEMPLOS = [
    "Qual o total de vendas líquidas por mês em 2019?",
    "Quais os 10 produtos mais vendidos em quantidade?",
    "Quais os 5 clientes com maior faturamento líquido em 2020?",
    "Qual o total de vendas por vendedor?",
]

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("📊 Agente de Vendas")
    st.caption("Pergunte em linguagem natural sobre o Star Schema de vendas.")

    show_sql = st.toggle("Mostrar SQL gerado", value=True)

    st.divider()
    st.subheader("Exemplos de perguntas")
    exemplo_clicado = None
    for exemplo in EXEMPLOS:
        if st.button(exemplo, use_container_width=True):
            exemplo_clicado = exemplo

    st.divider()
    st.subheader("Schema disponível")
    for tabela, colunas in ALLOWED_TABLES.items():
        with st.expander(tabela):
            st.write(", ".join(colunas))

    st.divider()
    if st.button("🗑️ Limpar conversa", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# ---------------------------------------------------------------------------
# Estado da conversa
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []


def render_assistant_payload(payload: dict, show_sql: bool):
    """Renderiza a resposta do agente dentro de um chat_message."""
    if not payload["success"]:
        st.error(payload["message"])
        return

    st.markdown(payload["explanation"])

    if show_sql and payload.get("sql"):
        with st.expander("Ver SQL gerado"):
            st.code(payload["sql"], language="sql")

    if payload["data"] is not None and not payload["data"].empty:
        st.dataframe(payload["data"], use_container_width=True)

    if payload.get("fig") is not None:
        st.plotly_chart(payload["fig"], use_container_width=True)


# ---------------------------------------------------------------------------
# Histórico
# ---------------------------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            render_assistant_payload(msg["payload"], show_sql)
        else:
            st.markdown(msg["content"])

# ---------------------------------------------------------------------------
# Nova pergunta (input livre ou clique num exemplo)
# ---------------------------------------------------------------------------
pergunta = st.chat_input("Pergunte sobre vendas, clientes, produtos...") or exemplo_clicado

if pergunta:
    st.session_state.messages.append({"role": "user", "content": pergunta})
    with st.chat_message("user"):
        st.markdown(pergunta)

    with st.chat_message("assistant"):
        with st.spinner("Analisando..."):
            resultado = run_agent(pergunta, show_sql=show_sql)
        render_assistant_payload(resultado, show_sql)

    st.session_state.messages.append({"role": "assistant", "payload": resultado})
