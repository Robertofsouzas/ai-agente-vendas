"""
agent_core.py
Lógica central do Agente de IA para Análise de Vendas.
Importado pelo notebook (desenvolvimento) e pelo app.py (Streamlit / produção).
"""

import os
import re
import json
from typing import Optional, Dict, Any

import pandas as pd
import plotly.express as px
from dotenv import load_dotenv
from openai import OpenAI
from sqlalchemy import create_engine, text
import urllib.parse

load_dotenv()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_API_KEY,
)

MODEL_NAME = "nvidia/nemotron-3-ultra-550b-a55b"

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


engine = get_engine()


# ---------------------------------------------------------------------------
# Segurança de SQL — aceita SELECT e WITH (CTE), valida contra a whitelist
# ---------------------------------------------------------------------------
def is_safe_sql(sql: str):
    """Valida se a query é segura. Retorna (é_seguro, motivo_se_não_for)."""
    sql_upper = sql.upper().strip()

    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
                 "TRUNCATE", "EXEC", "EXECUTE", "CREATE", "GRANT", "REVOKE",
                 "--", ";--"]
    for cmd in forbidden:
        if cmd in sql_upper:
            return False, f"A consulta contém um comando não permitido: {cmd}"

    if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
        return False, "A consulta gerada não é um SELECT (ou CTE) válido."

    # Nomes de CTE contam como "tabelas" válidas só dentro desta query
    cte_names = set(re.findall(r'(?:WITH|,)\s*(\w+)\s+AS\s*\(', sql_upper))

    for match in re.findall(r'\bFROM\s+(\w+)|\bJOIN\s+(\w+)', sql_upper):
        table = match[0] or match[1]
        if table and table not in ALLOWED_TABLES and table not in cte_names:
            return False, f"Tabela não permitida na consulta: {table}"

    return True, ""


def execute_safe_query(sql: str) -> pd.DataFrame:
    """Executa query apenas se for segura."""
    is_safe, reason = is_safe_sql(sql)
    if not is_safe:
        raise ValueError(reason)

    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn)
    return df


# ---------------------------------------------------------------------------
# NVIDIA (Nemotron 3 Ultra) — geração de SQL
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
4. Prefira agregações (SUM, COUNT, AVG) quando fizer sentido. Use CTEs (WITH) quando precisar
   comparar um subconjunto contra um total (ex: percentuais de participação).
5. Use aliases claros e consistentes — nunca reutilize o mesmo alias com grafias diferentes.
6. Para datas use MOVIMENTO.
7. Se a pergunta não puder ser respondida com o schema, retorne sql = null e explique.

SCHEMA:
{SCHEMA_DESCRIPTION}

PERGUNTA DO USUÁRIO:
{user_question}
"""


def ask_nvidia(question: str) -> Dict[str, Any]:
    """Envia a pergunta (ou um prompt de correção já formatado) pro modelo
    e retorna o JSON estruturado com intention/sql/needs_chart/chart_type/explanation."""
    prompt = question
    if "SCHEMA:" not in question:
        prompt = build_prompt(question)

    response_text = None
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": "Você é um especialista em SQL Server e análise de dados."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0,
            max_tokens=2000,
            extra_body={
                "chat_template_kwargs": {
                    "enable_thinking": False
                }
            }
        )

        response_text = response.choices[0].message.content.strip()

        # Remove eventual bloco Markdown ```json ... ```
        response_text = re.sub(
            r"^```json\s*|\s*```$",
            "",
            response_text,
            flags=re.IGNORECASE
        ).strip()

        return json.loads(response_text)

    except json.JSONDecodeError:
        return {
            "intention": "erro de parsing",
            "sql": None,
            "needs_chart": False,
            "chart_type": "none",
            "explanation": response_text
        }

    except Exception as e:
        return {
            "intention": "erro na chamada da NVIDIA",
            "sql": None,
            "needs_chart": False,
            "chart_type": "none",
            "explanation": str(e)
        }


def generate_insights(
    question: str,
    df: pd.DataFrame,
    intention: str = ""
) -> str:

    if df.empty:
        return "A consulta não retornou dados para análise."

    # Limita os dados enviados ao modelo
    df_analysis = df.head(100)

    data = df_analysis.to_json(
        orient="records",
        force_ascii=False
    )

    prompt = f"""
Você é um analista de dados sênior especializado em
análise de desempenho comercial e geração de insights
executivos..

Analise os dados abaixo e gere insights objetivos para ajudar
na interpretação dos resultados.

PERGUNTA DO USUÁRIO:
{question}

INTENÇÃO DA ANÁLISE:
{intention}

DADOS DA CONSULTA:
{data}

REGRAS:
- Baseie-se somente nos dados apresentados.
- Não invente informações.
- Identifique tendências, maiores valores, menores valores,
  diferenças relevantes e concentrações.
- Se houver evolução temporal, identifique crescimento ou queda.
- Quando possível, mencione os valores encontrados.
- Gere no máximo 5 insights.
- Seja objetivo, analítico e profissional.
- Escreva em português.
- Não utilize Markdown.
- Não utilize asteriscos (*).
- Não utilize emojis.
- Evite termos exagerados ou sensacionalistas como
  "abismal", "extremo", "absurdo", "alarmante" ou similares.
- Prefira linguagem executiva, clara e neutra.
- Não repita simplesmente os dados da tabela.
- Cada insight deve destacar uma conclusão relevante
  e explicar brevemente o que ela representa para a análise.

FORMATO:
1. Título curto: explicação objetiva.
2. Título curto: explicação objetiva.
3. Título curto: explicação objetiva.
"""

    try:

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": "Você é um analista de dados especializado em vendas."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2,
            max_tokens=1000,
            extra_body={
                "chat_template_kwargs": {
                    "enable_thinking": False
                }
            }
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        return f"Não foi possível gerar os insights: {str(e)}"


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
    else:
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
# Orquestrador — com autocorreção de SQL
# ---------------------------------------------------------------------------
def run_agent(question: str, max_sql_attempts: int = 2) -> Dict[str, Any]:
    """
    pergunta → NVIDIA → SQL → validação → SQL Server → DataFrame → insights + gráfico
    Se a execução falhar, tenta corrigir automaticamente pedindo pro modelo revisar.
    """
    try:
        analysis = ask_nvidia(question)
        sql = analysis.get("sql")

        if not sql:
            return {
                "success": False,
                "message": analysis.get("explanation", "Não foi possível gerar uma consulta SQL."),
            }

        df = None
        last_error = None

        for attempt in range(max_sql_attempts):
            is_safe, reason = is_safe_sql(sql)
            if not is_safe:
                return {"success": False, "message": reason, "sql": sql}

            try:
                with engine.connect() as conn:
                    df = pd.read_sql(text(sql), conn)
                break

            except Exception as e:
                last_error = str(e)

                if attempt == max_sql_attempts - 1:
                    return {
                        "success": False,
                        "message": f"Erro ao executar a consulta: {last_error}",
                        "sql": sql,
                    }

                fix_prompt = f"""
A query SQL abaixo falhou ao ser executada no SQL Server, com o erro:
{last_error}

QUERY ORIGINAL:
{sql}

Corrija a query mantendo a mesma intenção da pergunta original: "{question}"
Responda APENAS com o JSON no mesmo formato de antes (intention, sql, needs_chart, chart_type, explanation).

SCHEMA:
{SCHEMA_DESCRIPTION}
"""
                analysis = ask_nvidia(fix_prompt)
                sql = analysis.get("sql")

                if not sql:
                    return {"success": False, "message": "Não foi possível corrigir a consulta automaticamente."}

        insights = generate_insights(question=question, df=df, intention=analysis.get("intention", ""))

        fig = None
        if analysis.get("needs_chart", False):
            fig = create_chart(
                df=df,
                chart_type=analysis.get("chart_type", "bar"),
                title=analysis.get("intention", "Análise de vendas"),
            )

        return {
            "success": True,
            "message": "Consulta executada com sucesso.",
            "question": question,
            "intention": analysis.get("intention"),
            "sql": sql,
            "explanation": analysis.get("explanation"),
            "data": df,
            "row_count": len(df),
            "insights": insights,
            "fig": fig,
        }

    except Exception as e:
        return {"success": False, "message": f"Erro ao executar o agente: {str(e)}"}
