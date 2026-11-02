"""Benchmark reproduzível com dados sintéticos e resultado esperado independente."""
import argparse
import io
import json
from pathlib import Path
import platform
import statistics
from time import perf_counter

import pandas as pd

from datacheck.engine import audit_table, orders_rules
from datacheck.io import read_table


def synthetic(size):
    """Gera bytes CSV de pedidos fictícios com erros em posições conhecidas.

    Args:
        size: Número de registros a gerar.

    Returns:
        CSV UTF-8 com as colunas esperadas por orders_rules. Em cada grupo de
        cem registros, as cinco primeiras posições recebem, respectivamente,
        valor negativo, ausência, número inválido, data impossível e sequência
        invertida. Os demais registros são válidos para essas regras.
    """
    rows = []
    for i in range(size):
        row = [f"P{i:06}", "Cliente fictício", "50.00", "2026-01-10", "2026-01-12"]
        category = i % 100
        if category == 0:
            row[2] = "-10"
        elif category == 1:
            row[1] = ""
        elif category == 2:
            row[2] = "inválido"
        elif category == 3:
            row[3] = "2026-02-30"
        elif category == 4:
            row[4] = "2026-01-09"
        rows.append(row)
    return pd.DataFrame(rows, columns=["pedido_id", "cliente", "valor", "data_compra", "data_entrega"]).to_csv(index=False).encode()


def run(sizes=(1000, 10000, 50000), repeats=3):
    """Mede separadamente a leitura e a auditoria, conferindo as ocorrências injetadas.

    Args:
        sizes: Tamanhos positivos compatíveis com os limites de read_table.
        repeats: Quantidade positiva de execuções por tamanho.

    Returns:
        Relatório com ambiente, método, contagens esperadas/observadas e
        medianas em milissegundos. Esta função não grava o relatório.

    Raises:
        AssertionError: O motor não encontra a quantidade prevista de problemas.

    Não mede interface, exportação, IA ou comparação com trabalho humano.
    A contagem esperada é derivada das posições injetadas, não do auditor.
    """
    output = {"python": platform.python_version(), "platform": platform.system(), "processor": platform.processor(),
              "repeats": repeats, "method": "mediana; leitura CSV e motor medidos separadamente; dados sintéticos; sem IA ou comparação humana", "results": []}
    for size in sizes:
        raw = synthetic(size)
        read_ms, audit_ms = [], []
        for _ in range(repeats):
            start = perf_counter()
            table = read_table(raw, "benchmark.csv")
            read_ms.append((perf_counter() - start) * 1000)
            result = audit_table(table, orders_rules())
            audit_ms.append(result.summary["elapsed_ms"])
            expected = sum(1 for i in range(size) if i % 100 < 5)
            assert result.summary["findings"] == expected
            assert result.summary["affected_rows"] == expected
        output["results"].append({"rows": size, "bytes": len(raw), "expected_findings": expected, "observed_findings": result.summary["findings"],
                                  "read_median_ms": round(statistics.median(read_ms), 2), "audit_median_ms": round(statistics.median(audit_ms), 2)})
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("reports/benchmark.json"))
    parser.add_argument("--smoke", action="store_true", help="Apenas 1.000 registros, uma repetição")
    args = parser.parse_args()
    report = run((1000,), 1) if args.smoke else run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
