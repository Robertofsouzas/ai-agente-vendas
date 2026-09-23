
# Agente de IA para Análise de Vendas

Agente conversacional que responde perguntas em linguagem natural sobre vendas, convertendo-as automaticamente em consultas SQL validadas contra um Data Warehouse em modelo dimensional (Star Schema).

Pergunte "Qual o total de vendas líquidas por mês em 2019?" e o agente gera o SQL, executa com segurança no banco, e devolve tabela + gráfico automaticamente.

## Arquitetura

```
Usuário → Streamlit (chat) → Gemini (NL → SQL) → Validação de segurança
                                                        ↓
                                              SQL Server (Star Schema)
                                                        ↓
                                       Pandas + Plotly (tabela + gráfico)
```

- **Interface**: Streamlit (chat)
- **LLM**: Google Gemini (`gemini-3.6-flash`)
- **Banco**: SQL Server, modelo dimensional (fato + dimensões)
- **Camada de segurança**: whitelist de tabelas/colunas + validação de SQL antes da execução (bloqueia `INSERT`, `UPDATE`, `DELETE`, `DROP` etc. — só `SELECT` é permitido)

## Modelo de dados

**Fato:**
- `F_VENDAS` — quantidade, venda bruta, desconto, venda líquida, por data/cliente/produto/vendedor/empresa

**Dimensões:**
- `D_CLIENTE`, `D_EMPRESA`, `D_PRODUTO`, `D_VENDEDOR`

## Como rodar localmente

### 1. Pré-requisitos

- Python 3.10+
- SQL Server acessível com o Star Schema populado
- Chave de API do Gemini ([ai.google.dev](https://ai.google.dev))

### 2. Clonar e instalar dependências

```bash
git clone https://github.com/SEU-USUARIO/ai-agente-vendas.git
cd ai-agente-vendas
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux/Mac
pip install -r requirements.txt
```

### 3. Configurar variáveis de ambiente

Copie `.env.example` para `.env` e preencha com seus valores:

```bash
copy .env.example .env
```

```
GEMINI_API_KEY=sua_chave_aqui
SQL_CONNECTION_STRING=sua_connection_string_aqui
```

### 4. Rodar

```bash
streamlit run app.py
```

Acesse `http://localhost:8501` no navegador.

## Estrutura do projeto

```
├── app.py              # Interface Streamlit (chat)
├── agent_core.py        # Lógica do agente: schema, Gemini, execução SQL, gráficos
├── notebook.ipynb       # Desenvolvimento e testes exploratórios
├── requirements.txt
├── .env.example
└── README.md
```

## Segurança

- Todo SQL gerado pelo LLM passa por validação antes de ser executado: apenas comandos `SELECT`, apenas tabelas/colunas da whitelist definida em `ALLOWED_TABLES`.
- Comandos destrutivos (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, etc.) são bloqueados independentemente do que o modelo gerar.

## Roadmap

- [ ] Migrar geração de SQL para a Interactions API do Gemini (JSON Schema validado nativamente)
- [ ] Cache de perguntas frequentes
- [ ] Deploy em produção (Streamlit Community Cloud)
- [ ] Testes automatizados para `is_safe_sql`

## Autor

Roberto Souza (Beto) — BI Data Analyst & Analytics Engineer

