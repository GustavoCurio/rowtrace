"""Execução em lote: python -m datacheck arquivo.csv --orders --output relatorio."""
import argparse
import json
from pathlib import Path

from .engine import Rules, audit_table, orders_rules
from .io import MAX_BYTES, read_table
from .reports import findings_csv, report_html, report_json


def main():
    """Executa uma auditoria em lote e grava HTML, JSON e CSV na pasta escolhida.

    --orders aplica a demonstração; --rules lê configuração JSON. Sem ambos,
    apenas duplicatas completas são verificadas. O arquivo é lido de forma
    limitada. ValueError/OSError produzem mensagem no terminal e saída 2;
    erros de argumentos são tratados pelo argparse. O sucesso imprime o resumo.
    """
    parser = argparse.ArgumentParser(description="DataCheck: validação local de CSV/XLSX")
    parser.add_argument("file", type=Path)
    config = parser.add_mutually_exclusive_group()
    config.add_argument("--orders", action="store_true", help="Aplicar regras da demonstração de pedidos")
    config.add_argument("--rules", type=Path, help="Configuração JSON das regras")
    parser.add_argument("--output", type=Path, default=Path("relatorio"))
    parser.add_argument("--delimiter", choices=["auto", ",", ";", "\t"], default="auto")
    parser.add_argument("--encoding", choices=["utf-8-sig", "cp1252"], default="utf-8-sig")
    parser.add_argument("--sheet", type=int, default=0)
    args = parser.parse_args()
    try:
        with args.file.open("rb") as source:
            content = source.read(MAX_BYTES + 1)
        table = read_table(content, args.file.name, args.delimiter, args.encoding, args.sheet)
        rules = orders_rules() if args.orders else Rules.model_validate_json(args.rules.read_text("utf-8")) if args.rules else Rules()
        result = audit_table(table, rules)
        args.output.mkdir(parents=True, exist_ok=True)
        for name, data in [("relatorio.json", report_json(result)), ("relatorio.html", report_html(result)), ("ocorrencias.csv", findings_csv(result))]:
            (args.output / name).write_bytes(data)
        print(json.dumps(result.summary, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(2, f"DataCheck: {error}\n")


if __name__ == "__main__":
    main()
