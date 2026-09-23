"""
agent_core.py
Lógica central do Agente de IA para Análise de Vendas.
Importado pelo notebook (desenvolvimento) e pelo app.py (Streamlit / produção).
"""

import os
import re
import json
import time
import random
import urllib.parse
from typing import Optional, Dict, Any

import pandas as pd
import plotly.express as px
from dotenv import load_dotenv
from google import genai
from google.genai import types
from sqlalchemy import create_engine, text

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

MODEL_NAME = "gemini-3.6-flash"

# ---------------------------------------------------------------------------
# Schema permitido — nunca deixe o LLM inventar tabelas/colunas
# ---------------------------------------------------------------------------
ALLOWED_TABLES = {
    "D_CLIENTE": ["COD_CLIENTE", "NOME", "NOME_FANTASIA", "CLASSIFICACAO_CLIENTE", "CIDADE", "ESTADO", "UF"],
    "D_EMPRESA": ["COD_EMPRESA", "NOME", "NOME_FANTASIA"],
    "D_PRODUTO": ["COD_PRODUTO", "DESCRICAO", "DESCRICAO_REDUZIDA", "FAMILIA", "SECAO", "GRUPO", "SUB_GRUPO", "MARCA"],
    "D_VENDEDOR": ["COD_VENDEDOR", "NOME"],
    "F_VENDAS": ["N_DOC", "COD_EMPRESA", "COD_PRODUTO", "COD_VENDEDOR", "COD_CLIENTE",
                 "MOVIMENTO", "QUANTIDADE", "VENDA_BRUTA", "DESCONTO_TOTAL", "VENDA_LIQUIDA"],
}

SCHEMA_DESCRIPTION = """
Star Schema de Vendas:

DIMENSÕES:
- D_CLIENTE (COD_CLIENTE PK): NOME, NOME_FANTASIA, CLASSIFICACAO_CLIENTE, CIDADE, ESTADO, UF
- D_EMPRESA (COD_EMPRESA PK): NOME, NOME_FANTASIA
- D_PRODUTO (COD_PRODUTO PK): DESCRICAO, DESCRICAO_REDUZIDA, FAMILIA, SECAO, GRUPO, SUB_GRUPO, MARCA
- D_VENDEDOR (COD_VENDEDOR PK): NOME

FATO:
- F_VENDAS: N_DOC, COD_EMPRESA, COD_PRODUTO, COD_VENDEDOR, COD_CLIENTE, MOVIMENTO (date),
            QUANTIDADE (int), VENDA_BRUTA (money), DESCONTO_TOTAL (money), VENDA_LIQUIDA (money)

Joins típicos:
F_VENDAS.COD_CLIENTE = D_CLIENTE.COD_CLIENTE
F_VENDAS.COD_EMPRESA = D_EMPRESA.COD_EMPRESA
F_VENDAS.COD_PRODUTO = D_PRODUTO.COD_PRODUTO
F_VENDAS.COD_VENDEDOR = D_VENDEDOR.COD_VENDEDOR
"""


# ---------------------------------------------------------------------------
# Conexão com o banco
# ---------------------------------------------------------------------------
def get_engine():
    connection_string = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=localhost\\SQLEXPRESS;"
        "DATABASE=DW;"
        "Trusted_Connection=yes;"
    )
    params = urllib.parse.quote_plus(connection_string)
    return create_engine(f"mssql+pyodbc:///?odbc_connect={params}")


# ---------------------------------------------------------------------------
# Segurança de SQL
# ---------------------------------------------------------------------------
def is_safe_sql(sql: str) -> bool:
    """Valida se a query é segura (apenas SELECT e tabelas/colunas permitidas)."""
    sql_upper = sql.upper().strip()

    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
                 "EXEC", "EXECUTE", "CREATE", "GRANT", "REVOKE", "--", ";--"]
    if any(cmd in sql_upper for cmd in forbidden):
        return False

    if not sql_upper.startswith("SELECT"):
        return False

    for table in re.findall(r'\bFROM\s+(\w+)|\bJOIN\s+(\w+)', sql_upper):
        t = table[0] or table[1]
        if t and t not in ALLOWED_TABLES:
            return False

    return True


def execute_safe_query(sql: str) -> pd.DataFrame:
    """Executa query apenas se for segura."""
    if not is_safe_sql(sql):
        raise ValueError("Query rejeitada por segurança. Apenas SELECT com tabelas permitidas.")

    engine = get_engine()
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn)
    return df


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
def build_prompt(user_question: str) -> str:
    return f"""
Você é um analista de dados especializado em vendas.
Seu único trabalho é converter a pergunta do usuário em uma query SQL Server válida e segura.

REGRAS OBRIGATÓRIAS:
1. Responda APENAS com um JSON no formato:
{{
  "intention": "descrição curta da intenção",
  "sql": "SELECT ...",
  "needs_chart": true/false,
  "chart_type": "bar|line|pie|table|none",
  "explanation": "explicação curta do que a query faz"
}}
2. Use APENAS as tabelas e colunas do schema abaixo.
3. Nunca use INSERT, UPDATE, DELETE, DROP, etc.
4. Prefira agregações (SUM, COUNT, AVG) quando fizer sentido.
5. Use aliases claros.
6. Para datas use MOVIMENTO.
7. Se a pergunta não puder ser respondida com o schema, retorne sql = null e explique.

SCHEMA:
{SCHEMA_DESCRIPTION}

PERGUNTA DO USUÁRIO:
{user_question}
"""


def ask_gemini(question: str, max_retries: int = 3) -> Dict[str, Any]:
    prompt = build_prompt(question)

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            return json.loads(response.text)

        except json.JSONDecodeError:
            return {
                "intention": "erro de parsing",
                "sql": None,
                "needs_chart": False,
                "chart_type": "none",
                "explanation": response.text,
            }

        except Exception as e:
            is_last_attempt = attempt == max_retries - 1
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                if is_last_attempt:
                    return {
                        "intention": "erro de disponibilidade",
                        "sql": None,
                        "needs_chart": False,
                        "chart_type": "none",
                        "explanation": "O modelo Gemini está indisponível no momento (alta demanda). Tente novamente em instantes.",
                    }
                wait = (2 ** attempt) + random.uniform(0, 1)
                time.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# Gráficos
# ---------------------------------------------------------------------------
def create_chart(df: pd.DataFrame, chart_type: str, title: str, category_threshold: int = 8):
    """Cria gráfico Plotly. Acima de `category_threshold` categorias, usa barra horizontal
    para melhor leitura dos rótulos."""
    if df.empty:
        return None

    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    categorical_cols = df.select_dtypes(exclude="number").columns.tolist()

    if not numeric_cols:
        return None

    if categorical_cols:
        x_col = categorical_cols[0]
        y_candidates = numeric_cols
    else:
        x_col = numeric_cols[0]
        y_candidates = numeric_cols[1:] or numeric_cols

    y_col = next((c for c in y_candidates if c != x_col), numeric_cols[0])
    n_categories = df[x_col].nunique()

    if chart_type == "pie":
        fig = px.pie(df.sort_values(by=x_col), names=x_col, values=y_col, title=title)

    elif chart_type == "line":
        fig = px.line(df.sort_values(by=x_col), x=x_col, y=y_col, title=title)

    else:  # bar (default)
        if n_categories > category_threshold:
            df_sorted = df.sort_values(by=y_col, ascending=True)
            fig = px.bar(df_sorted, x=y_col, y=x_col, orientation="h", title=title)
            fig.update_layout(height=max(400, n_categories * 28))
        else:
            df_sorted = df.sort_values(by=x_col)
            fig = px.bar(df_sorted, x=x_col, y=y_col, title=title)

    fig.update_layout(template="plotly_white")
    return fig


# ---------------------------------------------------------------------------
# Orquestrador
# ---------------------------------------------------------------------------
def run_agent(question: str, show_sql: bool = True) -> Dict[str, Any]:
    gemini_result = ask_gemini(question)

    sql = gemini_result.get("sql")
    if not sql:
        return {
            "success": False,
            "message": gemini_result.get("explanation", "Não foi possível gerar SQL."),
            "data": None,
            "fig": None,
        }

    try:
        df = execute_safe_query(sql)
    except Exception as e:
        return {
            "success": False,
            "message": f"Erro ao executar query: {str(e)}",
            "data": None,
            "fig": None,
        }

    fig = None
    if gemini_result.get("needs_chart") and not df.empty:
        fig = create_chart(df, gemini_result.get("chart_type", "bar"), question)

    return {
        "success": True,
        "intention": gemini_result.get("intention"),
        "explanation": gemini_result.get("explanation"),
        "sql": sql,
        "data": df,
        "fig": fig,
        "row_count": len(df),
    }
