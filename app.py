"""Interface de diagnóstico, configuração e exportação do RowTrace."""
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from rowtrace.ai import explain_ai
from rowtrace.engine import Rules, audit_table
from rowtrace.io import read_table
from rowtrace.reports import LABELS, explain_local, findings_csv, report_html, report_json


ROOT = Path(__file__).parent
st.set_page_config(page_title="RowTrace · Auditoria de arquivos", page_icon="◈", layout="wide")
st.markdown("""<style>
.stApp{background:#faf9f6;color:#292721}.block-container{max-width:1330px;padding-top:4rem}
h1,h2,h3{letter-spacing:-.04em}h1{font-size:2.6rem!important}
.audit-brand{font-size:15px;font-weight:800;letter-spacing:2px;color:#b3542c}
.steps{display:flex;gap:12px;margin:22px 0 30px}.step{flex:1;border-top:3px solid #dedbd4;padding:12px 0;color:#948e83;font-size:14px}
.step.active{border-color:#c66338;color:#994220;font-weight:700}.step.done{border-color:#292721;color:#292721}
.audit-summary{background:#292721;color:#fffaf0;padding:26px;border-radius:8px;margin:14px 0 24px}
.audit-summary strong{font-size:46px;font-weight:500;display:block;line-height:1.15}
.audit-summary small{color:#cfc5b7}.audit-summary p{margin:12px 0 0}
[data-testid="stDataFrame"]{border:1px solid #dedbd4;border-radius:8px}
@media(max-width:700px){.step{font-size:12px}.steps{gap:6px}}
</style>""", unsafe_allow_html=True)
st.markdown('<div class="audit-brand">◈ ROWTRACE / AUDITORIA DE DADOS</div>', unsafe_allow_html=True)
# A navegação usa estado da sessão para manter arquivo e regras entre as etapas.
step = st.session_state.get("audit_step", 1)
steps = ["Escolher arquivo", "Definir regras", "Conferir resultado"]
st.markdown('<div class="steps">' + ''.join(f'<div class="step {"active" if i == step else "done" if i < step else ""}">0{i} &nbsp; {label}</div>' for i,label in enumerate(steps,1)) + '</div>', unsafe_allow_html=True)

if step == 1:
    st.title("Comece pela sua base.")
    st.write("Envie uma planilha ou experimente a demonstração. Você escolhe o que será verificado na próxima etapa.")
    entry, help_panel = st.columns([1.8, 1], gap="large")
    with entry:
        with st.container(border=True):
            source = st.radio("Fonte", ["Demonstração de pedidos", "Enviar meu arquivo"])
            is_demo = source == "Demonstração de pedidos"
            delimiter, encoding, sheet = "auto", "utf-8-sig", 0
            content, name = None, None
            if is_demo:
                content = (ROOT / "data/pedidos_demo.csv").read_bytes()
                name = "pedidos_demo.csv"
                st.write("10 pedidos fictícios com erros conhecidos: duplicatas, datas e valores inconsistentes.")
                st.download_button("Baixar base de exemplo", content, name, "text/csv")
            else:
                uploaded = st.file_uploader("Arquivo CSV ou XLSX", type=["csv","xlsx"])
                if uploaded:
                    content, name = uploaded.getvalue(), uploaded.name
                    if name.lower().endswith(".csv"):
                        delimiter = st.selectbox("Separador", ["auto", ",", ";", "\t"], format_func=lambda v: {"auto":"Detectar automaticamente", "\t":"Tabulação"}.get(v,v))
                        encoding = st.selectbox("Codificação", ["utf-8-sig","cp1252"])
                    else:
                        sheet = st.number_input("Número da aba", min_value=1, max_value=100, value=1)-1
            if st.button("Continuar para as regras →", type="primary", disabled=content is None):
                try:
                    table = read_table(content, name, delimiter, encoding, int(sheet))
                    new_key = hashlib.sha256(content + f"{delimiter}|{encoding}|{sheet}".encode()).hexdigest()[:12]
                    if st.session_state.get("dataset_key") != new_key:
                        st.session_state.pop("audit_rules", None)
                        st.session_state.pop("result", None)
                        st.session_state.pop("ai_explanation", None)
                    st.session_state.update(dataset_key=new_key, audit_input=(content,name,delimiter,encoding,int(sheet),is_demo), audit_step=2)
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))
    with help_panel:
        st.subheader("Uma auditoria, três passos")
        st.write("**01 / Arquivo**\n\nCSV ou Excel, processado em memória.")
        st.write("**02 / Regras**\n\nDefina campos obrigatórios, identificadores, números e datas.")
        st.write("**03 / Evidências**\n\nVeja os registros afetados e baixe o relatório.")
        st.caption("Até 10 MB · 50 mil registros · 50 colunas · 500 mil células.")
    st.stop()

content, name, delimiter, encoding, sheet, is_demo = st.session_state["audit_input"]
table = read_table(content, name, delimiter, encoding, sheet)
dataset_key = st.session_state["dataset_key"]
columns = list(table.columns)
# Widgets ocultos entre etapas podem perder estado; a configuração salva repõe seus valores.
cfg = st.session_state.get("audit_rules", {})
pair = cfg.get("date_pairs", [["data_compra", "data_entrega"]] if is_demo else [])
pair = pair[0] if pair else None
if step == 2:
    st.title("O que precisa estar correto?")
    st.caption(f"{name} · {len(table)} registros · {len(columns)} colunas")
    with st.expander("Conferir amostra do arquivo"):
        st.dataframe(table.head(20), hide_index=True, width="stretch")
    with st.expander("Configurar regras de validação", expanded=True):
        left, right = st.columns(2)
        with left:
            required = st.multiselect("Campos obrigatórios", columns, default=cfg.get("required", ["pedido_id", "cliente", "valor", "data_compra"] if is_demo else columns), key=f"required_{dataset_key}")
            key = st.selectbox("Identificador único", [None] + columns, index=([None] + columns).index(cfg.get("key", "pedido_id" if is_demo else None)), format_func=lambda v: v or "Não verificar identificador", key=f"key_{dataset_key}")
            numeric = st.multiselect("Colunas numéricas", columns, default=cfg.get("numeric", ["valor"] if is_demo else []), key=f"numeric_{dataset_key}")
            nonnegative = st.multiselect("Valores não negativos", numeric, default=[c for c in cfg.get("nonnegative", numeric) if c in numeric], key=f"positive_{dataset_key}_{','.join(numeric)}")
            number_style = st.selectbox("Formato dos números", ["decimal_dot", "decimal_comma"], index=0 if cfg.get("number_style", "decimal_dot") == "decimal_dot" else 1, format_func=lambda v: "1,234.56 — decimal com ponto" if v == "decimal_dot" else "1.234,56 — decimal com vírgula", key=f"numbers_{dataset_key}")
        with right:
            dates = st.multiselect("Colunas de data", columns, default=cfg.get("dates", ["data_compra", "data_entrega"] if is_demo else []), key=f"dates_{dataset_key}")
            date_format = st.selectbox("Formato das datas em texto", ["%Y-%m-%d", "%d/%m/%Y"], index=0 if cfg.get("date_format", "%Y-%m-%d") == "%Y-%m-%d" else 1, format_func=lambda v: "AAAA-MM-DD" if v == "%Y-%m-%d" else "DD/MM/AAAA", key=f"format_{dataset_key}")
            pair_key = f"{dataset_key}_{','.join(dates)}"
            first = st.selectbox("Data inicial da sequência", [None] + dates, index=([None] + dates).index(pair[0]) if pair and pair[0] in dates else 0, format_func=lambda v: v or "Sem comparação", key=f"first_{pair_key}")
            last = st.selectbox("Data final da sequência", [None] + dates, index=([None] + dates).index(pair[1]) if pair and pair[1] in dates else 0, format_func=lambda v: v or "Sem comparação", key=f"last_{pair_key}")
            exact = st.checkbox("Detectar registros completamente idênticos", value=cfg.get("exact_duplicates", True), key=f"exact_{dataset_key}")
        st.caption("Números e datas vazios são avaliados apenas quando o campo é obrigatório. Identificadores repetidos consideram o texto exato. Números e datas nativos do Excel são aceitos independentemente do formato textual escolhido.")
        if bool(first) != bool(last) or (first and first == last):
            st.warning("Escolha duas datas distintas para comparar ou deixe as duas sem comparação.")
            st.stop()
        rules = Rules(required=required, key=key, numeric=numeric, nonnegative=nonnegative, dates=dates,
                      date_pairs=[(first, last)] if first and last else [], number_style=number_style,
                      date_format=date_format, exact_duplicates=exact)


    back, submit = st.columns([1, 3])
    if back.button("← Trocar arquivo"):
        st.session_state.audit_step = 1
        st.rerun()
    if submit.button("Validar dados", type="primary"):
        with st.spinner("Verificando as regras…"):
            st.session_state.result = audit_table(table, rules)
            st.session_state.audit_rules = rules.model_dump(mode="json")
            st.session_state.pop("ai_explanation", None)
            st.session_state.audit_step = 3
        st.rerun()
    st.stop()

# As etapas anteriores interrompem o script até existir uma auditoria concluída.
result = st.session_state.result
s = result.summary
token = dataset_key + json.dumps(result.rules, sort_keys=True)
title, edit, change = st.columns([3,1,1])
title.title("Auditoria concluída.")
if edit.button("Editar regras"):
    st.session_state.audit_step = 2
    st.rerun()
if change.button("Outro arquivo"):
    st.session_state.audit_step = 1
    st.rerun()
st.caption(f"{name} · {s['rows']} registros analisados · {s['columns']} colunas · {s['elapsed_ms']} ms")
summary_panel, findings_panel = st.columns([1, 3], gap="large")
with summary_panel:
    st.markdown(f'<div class="audit-summary"><small>REGISTROS COM OCORRÊNCIAS</small><strong>{s["affected_rows"]} <small>/ {s["rows"]}</small></strong><p>{s["findings"]} ocorrências encontradas</p><p>{s["rows_without_findings_pct"]}% sem ocorrências nas regras selecionadas</p></div>', unsafe_allow_html=True)
    selected = st.radio("Tipo de problema", ["Todos"] + list(s["by_rule"]), format_func=lambda v: "Todos os problemas" if v == "Todos" else f"{LABELS[v][0]} · {s['by_rule'][v]}")
    st.caption("Um registro pode ter várias ocorrências. Os dados originais foram preservados.")
with findings_panel:
    st.subheader("Fila de correção")
    st.caption("Cada linha mostra a origem do problema e a verificação sugerida.")
    if result.findings:
        findings = pd.DataFrame([item for item in result.findings if selected == "Todos" or item["rule"] == selected])
        st.dataframe(findings.rename(columns={"record":"Registro","rule":"Regra","severity":"Severidade","column":"Coluna","value":"Valor","message":"Problema","suggestion":"Sugestão"}), column_order=["Registro","Coluna","Valor","Problema","Sugestão","Regra","Severidade"], hide_index=True, width="stretch", height=420)
    else:
        st.success(explain_local(result))
    st.caption("Cabeçalho = registro 1; primeiro registro de dados = 2. Em CSV com campos multilinha, a posição é lógica.")
    with st.expander("Ver diagnóstico e sugestões"):
        st.write(explain_local(result))
    with st.expander("Conferir dados originais"):
        st.dataframe(table.head(200), hide_index=True, width="stretch")
    with st.expander("Explicação com IA"):
        st.write("A IA pode organizar as prioridades usando apenas o total de registros e as contagens por regra. Valores das células, nomes das colunas e o arquivo não são enviados.")
        enabled = bool(os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_MODEL"))
        if not enabled:
            st.info("O diagnóstico local está disponível. A explicação com IA depende da configuração do provedor no servidor.")
        approved = st.checkbox("Autorizo enviar estas contagens agregadas à OpenAI.", key=f"consent_{token}")
        if st.button("Gerar explicação com IA", disabled=not (enabled and approved)):
            try:
                with st.spinner("Organizando as prioridades…"):
                    st.session_state.ai_explanation = explain_ai(result, approved=approved)
            except (ValueError, RuntimeError) as error:
                st.error(str(error))
        if "ai_explanation" in st.session_state:
            explanation = st.session_state.ai_explanation
            st.write(explanation["overview"])
            for priority in explanation["priorities"]:
                st.write(f"**{LABELS[priority['rule']][0]}**")
                st.write(priority["explanation"])
                st.write(priority["action"])
            st.caption("Texto gerado por IA; as contagens e evidências do relatório determinístico são a referência.")

st.divider()
with st.expander("Exportar relatório e configuração", expanded=True):
    st.subheader("Seu diagnóstico pronto para compartilhar")
    st.write("Baixe o relatório visual, as evidências para planilhas ou o resultado estruturado para integrar a outros sistemas.")
    a, b, c = st.columns(3)
    a.download_button("Relatório visual · HTML", report_html(result), "rowtrace-relatorio.html", "text/html", width="stretch")
    b.download_button("Ocorrências · CSV", findings_csv(result), "rowtrace-ocorrencias.csv", "text/csv", width="stretch")
    c.download_button("Resultado completo · JSON", report_json(result), "rowtrace-relatorio.json", "application/json", width="stretch")
    st.download_button("Configuração das regras · JSON", json.dumps(result.rules, ensure_ascii=False, indent=2), "regras.json", "application/json")
    st.caption("O CSV de ocorrências neutraliza textos que podem ser interpretados como fórmulas. O JSON preserva os valores originais.")
