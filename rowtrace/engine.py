"""Regras determinísticas e evidência por registro; SQL somente em memória."""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
import sqlite3
from time import perf_counter
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator


# Configuração das regras e de seus formatos; relações entre campos são validadas antes do uso.
class Rules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required: list[str] = Field(default_factory=list, max_length=50)
    key: str | None = None
    numeric: list[str] = Field(default_factory=list, max_length=50)
    nonnegative: list[str] = Field(default_factory=list, max_length=50)
    dates: list[str] = Field(default_factory=list, max_length=50)
    date_pairs: list[tuple[str, str]] = Field(default_factory=list, max_length=10)
    number_style: Literal["decimal_dot", "decimal_comma"] = "decimal_dot"
    date_format: Literal["%Y-%m-%d", "%d/%m/%Y"] = "%Y-%m-%d"
    exact_duplicates: bool = True

    @model_validator(mode="after")
    def consistent(self):
        """Valida relações entre as regras e devolve a própria configuração.

        Raises:
            ValueError: Uma coluna não negativa não foi declarada numérica, ou
                uma sequência compara colunas iguais/ausentes na lista de datas.

        A existência das colunas na tabela é conferida somente em audit_table.
        """
        if not set(self.nonnegative).issubset(self.numeric):
            raise ValueError("Colunas não negativas precisam estar nas colunas numéricas.")
        if any(a == b or a not in self.dates or b not in self.dates for a, b in self.date_pairs):
            raise ValueError("A sequência deve comparar duas colunas de data distintas.")
        return self


@dataclass
class Audit:
    """Resultado da auditoria com resumo, ocorrências, configuração e nomes das colunas."""
    summary: dict
    findings: list[dict]
    rules: dict
    columns: list[str]

    def to_dict(self):
        """Converte a auditoria e suas estruturas em dicionário para API e relatórios."""
        return asdict(self)


def parse_number(value, style):
    """Converte um valor textual em Decimal conforme uma gramática regional explícita.

    Args:
        value: Valor convertido em texto e aparado antes da leitura.
        style: decimal_dot usa 1,234.56; decimal_comma usa 1.234,56.

    Returns:
        Decimal finito, preservando o sinal e evitando aritmética de float.

    Raises:
        ValueError: Separadores, agrupamentos ou conteúdo não seguem a gramática.

    Aceita sinal opcional. Não aceita símbolo monetário, notação científica,
    NaN ou infinito. O chamador deve usar um dos estilos definidos em Rules.
    """
    text = str(value).strip()
    decimal, thousands = (".", ",") if style == "decimal_dot" else (",", ".")
    pattern = rf"[+-]?(?:\d+|\d{{1,3}}(?:{re.escape(thousands)}\d{{3}})+)(?:{re.escape(decimal)}\d+)?"
    if not re.fullmatch(pattern, text):
        raise ValueError("Formato numérico inválido")
    try:
        number = Decimal(text.replace(thousands, "").replace(decimal, "."))
        if not number.is_finite():
            raise ValueError("Número não finito")
        return number
    except InvalidOperation as error:
        raise ValueError("Número inválido") from error


def parse_date(value, fmt):
    """Valida formato e calendário e retorna datetime.date.

    Args:
        value: Data textual, com espaços externos removidos.
        fmt: Formato esperado para texto não ISO: %Y-%m-%d ou %d/%m/%Y.

    Returns:
        Data sem hora. Texto ISO AAAA-MM-DD é aceito independentemente de fmt,
        inclusive para datas nativas do Excel convertidas durante a leitura.

    Raises:
        ValueError: Formato incorreto ou data impossível no calendário.
    """
    text = str(value).strip()
    # Datas nativas do Excel chegam como ISO, independentemente do formato textual escolhido.
    chosen = "%Y-%m-%d" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else fmt
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}" if chosen == "%Y-%m-%d" else r"\d{2}/\d{2}/\d{4}", text):
        raise ValueError("Formato de data inválido")
    return datetime.strptime(text, chosen).date()


def _quoted(column):
    """Delimita identificador SQL e duplica aspas internas; valores não são tratados aqui."""
    return '"' + column.replace('"', '""') + '"'


def audit_table(frame: pd.DataFrame, rules: Rules):
    """Aplica as regras em uma cópia textual da tabela e produz evidências por posição.

    Args:
        frame: DataFrame não vazio com nomes de colunas únicos e textuais.
        rules: Configuração Rules já validada, com colunas presentes na tabela.

    Returns:
        Audit com ocorrências ordenadas por registro/regra/coluna, totais,
        percentuais, regras aplicadas e tempo do motor em milissegundos.

    Raises:
        ValueError: Tabela vazia, cabeçalhos inválidos ou coluna configurada ausente.

    Registros usam posições lógicas: cabeçalho = 1 e primeiro dado = 2.
    Duplicatas sinalizam todos os participantes, inclusive o primeiro.
    Números/datas vazios só geram ausência quando também são obrigatórios;
    datas inválidas não geram uma segunda ocorrência de sequência.
    O percentual sem ocorrências conta registros únicos, não células.
    A leitura e seus limites de arquivo devem ocorrer antes, em read_table.
    """
    started = perf_counter()
    if frame.empty or not frame.columns.is_unique or not all(isinstance(c, str) for c in frame.columns):
        raise ValueError("Forneça uma tabela não vazia com nomes de colunas únicos.")
    selected = set(rules.required + rules.numeric + rules.dates + ([rules.key] if rules.key else []))
    unknown = selected - set(frame.columns)
    if unknown:
        raise ValueError("Colunas não encontradas: " + ", ".join(sorted(unknown)))
    data = frame.fillna("").astype(str).reset_index(drop=True)
    findings = []
    native_numeric = {tuple(item) for item in frame.attrs.get("native_numeric", [])}

    def add(position, code, column, value, message, suggestion, severity="erro"):
        """Acrescenta uma evidência convertendo a posição de base zero para registro lógico."""
        findings.append({"record": int(position) + 2, "rule": code, "severity": severity,
                         "column": column, "value": str(value), "message": message, "suggestion": suggestion})

    with sqlite3.connect(":memory:") as connection:
        # Nomes internos controlados evitam tratar cabeçalhos recebidos como SQL.
        sql_names = {column: f"c{index}" for index, column in enumerate(data.columns)}
        data.rename(columns=sql_names).to_sql("dados", connection, index=False, if_exists="replace")
        if rules.exact_duplicates:
            partition = ", ".join(_quoted(c) for c in sql_names.values())
            rows = connection.execute(f"SELECT rowid FROM (SELECT rowid, COUNT(*) OVER (PARTITION BY {partition}) AS n FROM dados) WHERE n > 1").fetchall()
            for (record,) in rows:
                add(record - 1, "duplicate_row", "(registro completo)", "", "Registro idêntico a outro registro.", "Confirme a origem e mantenha apenas os registros necessários.", "atenção")
        if rules.key:
            key = _quoted(sql_names[rules.key])
            rows = connection.execute(f"SELECT rowid, {key} FROM dados WHERE TRIM({key}) <> '' AND {key} IN (SELECT {key} FROM dados WHERE TRIM({key}) <> '' GROUP BY {key} HAVING COUNT(*) > 1)").fetchall()
            for record, value in rows:
                add(record - 1, "duplicate_key", rules.key, value, "Identificador repetido.", "Confira se a chave deve identificar um único registro.")
    for column in dict.fromkeys(rules.required):
        for position, value in enumerate(data[column]):
            if not value.strip():
                add(position, "missing_required", column, value, "Campo obrigatório vazio.", "Preencha a partir da fonte original.")
    for column in dict.fromkeys(rules.numeric):
        for position, value in enumerate(data[column]):
            if not value.strip():
                continue
            try:
                style = "decimal_dot" if (position, column) in native_numeric else rules.number_style
                number = parse_number(value, style)
            except ValueError:
                add(position, "invalid_number", column, value, "Valor não segue o formato numérico escolhido.", "Confira o valor e os separadores decimal e de milhar.")
                continue
            if column in rules.nonnegative and number < 0:
                add(position, "negative_number", column, value, "Valor negativo em coluna definida como não negativa.", "Confira o lançamento na origem; não inverta o sinal automaticamente.")
    parsed_dates = {}
    for column in dict.fromkeys(rules.dates):
        parsed_dates[column] = {}
        for position, value in enumerate(data[column]):
            if not value.strip():
                continue
            try:
                parsed_dates[column][position] = parse_date(value, rules.date_format)
            except ValueError:
                add(position, "invalid_date", column, value, "Data inválida ou fora do formato escolhido.", "Confira a data e o formato selecionado.")
    for first, last in dict.fromkeys(rules.date_pairs):
        for position in parsed_dates[first].keys() & parsed_dates[last].keys():
            if parsed_dates[last][position] < parsed_dates[first][position]:
                add(position, "date_order", f"{first} → {last}", data.loc[position, last], "A data final ocorre antes da inicial.", "Confira as duas datas na origem.")
    findings.sort(key=lambda item: (item["record"], item["rule"], item["column"]))
    # Uma linha com vários erros afeta o percentual apenas uma vez.
    affected = len({item["record"] for item in findings})
    summary = {"rows": len(data), "columns": len(data.columns), "findings": len(findings),
               "affected_rows": affected, "rows_without_findings": len(data) - affected,
               "rows_without_findings_pct": round(100 * (len(data) - affected) / len(data), 2),
               "by_rule": dict(sorted(Counter(item["rule"] for item in findings).items())),
               "elapsed_ms": round((perf_counter() - started) * 1000, 2)}
    return Audit(summary, findings, rules.model_dump(mode="json"), list(data.columns))


def orders_rules():
    """Retorna uma nova configuração para as colunas da demonstração de pedidos."""
    return Rules(required=["pedido_id", "cliente", "valor", "data_compra"], key="pedido_id",
                 numeric=["valor"], nonnegative=["valor"], dates=["data_compra", "data_entrega"],
                 date_pairs=[("data_compra", "data_entrega")])
