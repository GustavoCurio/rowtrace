import csv
from datetime import datetime
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient
from openpyxl import Workbook
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from datacheck.ai import explain_ai
from datacheck.api import app
from datacheck.engine import Rules, audit_table, orders_rules, parse_number
from datacheck.io import MAX_BYTES, read_table
from datacheck.reports import findings_csv, report_html, report_json

ROOT = Path(__file__).resolve().parents[1]
DEMO = (ROOT / "data/pedidos_demo.csv").read_bytes()


def demo():
    return audit_table(read_table(DEMO, "demo.csv"), orders_rules())


def xlsx(rows):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def test_demo_exact_findings_and_positions():
    result = demo()
    assert result.summary["rows"] == 10
    assert result.summary["affected_rows"] == 7
    assert result.summary["findings"] == 9
    assert result.summary["rows_without_findings_pct"] == 30
    assert {(f["record"], f["rule"]) for f in result.findings} == {
        (3, "duplicate_row"), (4, "duplicate_row"), (3, "duplicate_key"), (4, "duplicate_key"),
        (5, "negative_number"), (6, "missing_required"), (7, "invalid_number"), (8, "invalid_date"), (9, "date_order")}


def test_valid_table_and_optional_missing():
    table = pd.DataFrame({"id": ["01", "02"], "value": ["0", ""], "date": ["2024-02-29", ""]})
    result = audit_table(table, Rules(key="id", numeric=["value"], dates=["date"]))
    assert result.findings == []
    assert result.summary["rows_without_findings_pct"] == 100


def test_required_blank_and_literal_na():
    table = read_table(b"id,name\n001,NA\n002,  \n003,NULL\n", "a.csv")
    assert table.loc[0, "id"] == "001"
    result = audit_table(table, Rules(required=["name"]))
    assert [(f["record"], f["rule"]) for f in result.findings] == [(3, "missing_required")]


def test_no_double_count_of_invalid_or_empty_numbers_and_dates():
    table = pd.DataFrame({"n": ["", "abc"], "a": ["", "2026-02-30"], "b": ["2026-01-01", "2026-01-01"]})
    result = audit_table(table, Rules(numeric=["n"], nonnegative=["n"], dates=["a", "b"], date_pairs=[("a", "b")]))
    assert result.summary["by_rule"] == {"invalid_date": 1, "invalid_number": 1}


@pytest.mark.parametrize("value,style,expected", [("1.234,56", "decimal_comma", "1234.56"), ("1,234.56", "decimal_dot", "1234.56"), ("-25,20", "decimal_comma", "-25.20"), ("0", "decimal_dot", "0")])
def test_number_formats(value, style, expected):
    assert str(parse_number(value, style)) == expected


@pytest.mark.parametrize("value", ["NaN", "inf", "1e3", "1,2,3", "R$ 20", "--1"])
def test_invalid_numeric_strings(value):
    with pytest.raises(ValueError):
        parse_number(value, "decimal_dot")


def test_dates_dmy_and_equal_dates():
    table = pd.DataFrame({"a": ["31/12/2025", "2026-01-01"], "b": ["30/12/2025", "2026-01-01"]})
    result = audit_table(table, Rules(dates=["a", "b"], date_format="%d/%m/%Y", date_pairs=[("a", "b")]))
    assert result.summary["by_rule"] == {"date_order": 1}


def test_sql_unusual_identifiers_and_rowid():
    table = pd.DataFrame({"rowid": ["A", "A"], 'a"; DROP TABLE dados; --': ["x", "x"]})
    result = audit_table(table, Rules(key="rowid"))
    assert result.summary["by_rule"] == {"duplicate_key": 2, "duplicate_row": 2}


def test_empty_keys_and_duplicate_rules():
    table = pd.DataFrame({"id": ["", " ", "x"], "n": ["abc", "1", "2"]})
    result = audit_table(table, Rules(key="id", numeric=["n", "n"]))
    assert result.summary["by_rule"] == {"invalid_number": 1}


@pytest.mark.parametrize("config", [{"numeric": [], "nonnegative": ["x"]}, {"dates": ["a"], "date_pairs": [["a", "b"]]}, {"extra": True}])
def test_invalid_rule_config(config):
    with pytest.raises(ValueError):
        Rules.model_validate(config)


def test_unknown_column():
    with pytest.raises(ValueError, match="Colunas não encontradas"):
        audit_table(pd.DataFrame({"x": ["1"]}), Rules(required=["missing"]))


def test_csv_semicolon_comma_decimal_and_multiline():
    content = 'id;valor;nota\n001;"1.234,50";"duas\nlinhas"\n002;-5;ok\n'.encode()
    table = read_table(content, "a.csv")
    result = audit_table(table, Rules(numeric=["valor"], nonnegative=["valor"], number_style="decimal_comma"))
    assert result.findings[0]["record"] == 3
    assert table.loc[0, "nota"] == "duas\nlinhas"


@pytest.mark.parametrize("content", [b"", b"a,a\nx,y\n", b"a,b\nx,y,z\n", b"a,b\n", b",b\nx,y\n", b"a\n\x00\n"])
def test_bad_csv(content):
    with pytest.raises(ValueError):
        read_table(content, "bad.csv")


def test_encoding_explicit():
    raw = "id,nome\n1,João\n".encode("cp1252")
    with pytest.raises(ValueError):
        read_table(raw, "a.csv")
    assert read_table(raw, "a.csv", encoding="cp1252").loc[0, "nome"] == "João"


@pytest.mark.parametrize("content,name", [(b"x" * (MAX_BYTES + 1), "a.csv"), (b"test", "a.xls"), (b"test", "a.xlsx"), (b"a\n" + b"x" * 2001, "a.csv"), (b"a\n" + b"x\n" * 50_001, "a.csv")], ids=["file_size", "unsupported", "broken_xlsx", "long_cell", "row_count"])
def test_ingestion_limits(content, name):
    with pytest.raises(ValueError):
        read_table(content, name)


def test_xlsx_dates_numeric_and_multiple_sheets():
    raw = xlsx([["id", "valor", "data"], ["001", 12.5, datetime(2026, 1, 1)]])
    table = read_table(raw, "a.xlsx")
    assert table.loc[0].to_dict() == {"id": "001", "valor": "12.5", "data": "2026-01-01"}
    assert not audit_table(table, Rules(numeric=["valor"], dates=["data"])).findings
    assert not audit_table(table, Rules(numeric=["valor"], dates=["data"], number_style="decimal_comma", date_format="%d/%m/%Y")).findings
    with pytest.raises(ValueError, match="aba selecionada"):
        read_table(raw, "a.xlsx", sheet=2)


def test_xlsx_formula_rejected():
    with pytest.raises(ValueError, match="fórmulas"):
        read_table(xlsx([["id", "value"], ["x", "=1+1"]]), "a.xlsx")


def test_reports_escape_and_csv_formula_neutralization():
    table = pd.DataFrame({"<script>alert(1)</script>": ["=HYPERLINK(\"https://example.com\")"]})
    result = audit_table(table, Rules(numeric=list(table.columns)))
    report = report_html(result).decode()
    assert "<script>" not in report and "&lt;script&gt;" in report
    row = list(csv.reader(io.StringIO(findings_csv(result).decode("utf-8-sig"))))[1]
    assert row[4].startswith("'=")
    assert json.loads(report_json(result))["findings"][0]["value"].startswith("=HYPERLINK")


def test_ai_aggregates_only_and_consent():
    result = demo()
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps({"overview": "Verifique as ocorrências.", "priorities": [{"rule": "duplicate_key", "explanation": "Há identificadores repetidos.", "action": "Confira a origem."}]}))
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    with pytest.raises(ValueError, match="Autorize"):
        explain_ai(result, client=client, model="configured-model")
    assert not calls
    output = explain_ai(result, approved=True, client=client, model="configured-model")
    payload = json.loads(calls[0]["input"])
    assert set(payload) == {"rows", "affected_rows", "findings", "by_rule"}
    assert "Cliente" not in calls[0]["input"] and "pedido_id" not in calls[0]["input"]
    assert calls[0]["store"] is False
    assert output["priorities"][0]["rule"] == "duplicate_key"


@pytest.mark.parametrize("text", ["not-json", '{"overview":"x","priorities":[{"rule":"invented","explanation":"x","action":"x"}]}', '{"overview":"x","priorities":[],"extra":"x"}'])
def test_ai_rejects_malformed_or_unrecognized_rule(text):
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(output_text=text)))
    with pytest.raises(RuntimeError):
        explain_ai(demo(), approved=True, client=client, model="configured-model")


def test_api_csv_and_invalid_rules():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.post("/audit", files={"file": ("demo.csv", DEMO, "text/csv")}, data={"rules": orders_rules().model_dump_json()})
        assert response.status_code == 200
        assert response.json()["summary"]["findings"] == 9
        response = client.post("/audit", files={"file": ("demo.csv", DEMO)}, data={"rules": "bad-json"})
        assert response.status_code == 422
        response = client.post("/audit", files={"file": ("bad.csv", b"a,a\nx,x\n")})
        assert response.status_code == 422


def test_api_xlsx():
    with TestClient(app) as client:
        response = client.post("/audit", files={"file": ("a.xlsx", xlsx([["id"], ["1"]]))})
        assert response.status_code == 200
        assert response.json()["summary"]["rows"] == 1


def test_cli_exports(tmp_path):
    target = tmp_path / "report"
    process = subprocess.run([sys.executable, "-m", "datacheck", str(ROOT / "data/pedidos_demo.csv"), "--orders", "--output", str(target)], capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    assert process.returncode == 0, process.stderr
    assert json.loads((target / "relatorio.json").read_text("utf-8"))["summary"]["findings"] == 9
    assert {f.name for f in target.iterdir()} == {"relatorio.json", "relatorio.html", "ocorrencias.csv"}


def test_ui_demo_and_changed_rules():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert len(at.metric) == 0
    next(b for b in at.button if b.label == "Continuar para as regras →").click().run()
    assert not at.exception
    assert any("O que precisa estar correto?" in title.value for title in at.title)
    next(c for c in at.checkbox if c.label == "Detectar registros completamente idênticos").uncheck().run()
    next(b for b in at.button if b.label == "Validar dados").click().run()
    assert not at.exception
    assert any("Auditoria concluída" in title.value for title in at.title)
    assert at.session_state["result"].summary["findings"] == 7
    assert at.session_state["result"].summary["affected_rows"] == 7
    assert len(at.dataframe[0].value) == 7
    next(b for b in at.button if b.label == "Editar regras").click().run()
    assert not at.exception
    assert not next(c for c in at.checkbox if c.label == "Detectar registros completamente idênticos").value
    next(c for c in at.checkbox if c.label == "Detectar registros completamente idênticos").check().run()
    next(b for b in at.button if b.label == "Validar dados").click().run()
    assert not at.exception
    assert at.session_state["result"].summary["findings"] == 9
    next(r for r in at.radio if r.label == "Tipo de problema").set_value("invalid_date").run()
    assert not at.exception
    assert len(at.dataframe[0].value) == 1
    next(b for b in at.button if b.label == "Outro arquivo").click().run()
    next(r for r in at.radio if r.label == "Fonte").set_value("Enviar meu arquivo").run()
    assert next(b for b in at.button if b.label == "Continuar para as regras →").disabled
