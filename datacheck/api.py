"""API local: multipart de arquivo e regras JSON; sem persistência de conteúdo."""
import json

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from .engine import Rules, audit_table
from .io import MAX_BYTES, read_table

app = FastAPI(title="DataCheck", version="1.0.0", description="Validação de CSV/XLSX com evidência por registro.")


@app.get("/health")
def health():
    """Retorna a disponibilidade da API sem ler arquivo ou executar auditoria."""
    return {"status": "ok"}


@app.post("/audit")
async def audit(file: UploadFile = File(...), rules: str = Form("{}"), delimiter: str = Form("auto"),
                encoding: str = Form("utf-8-sig"), sheet: int = Form(0)):
    """Valida arquivo multipart e configuração JSON e devolve a auditoria completa.

    Args:
        file: Upload CSV/XLSX, lido até o limite mais um byte para detectar excesso.
        rules: Objeto Rules serializado como texto JSON; {} ativa só duplicatas exatas.
        delimiter: Separador CSV ou auto.
        encoding: Codificação do CSV, conforme read_table.
        sheet: Índice de base zero da aba XLSX.

    Returns:
        Dicionário do resultado Audit, sem executar a explicação generativa.

    Raises:
        HTTPException: 422 quando leitura, configuração ou auditoria são inválidas.

    Fecha o UploadFile inclusive em falha. Não persiste o conteúdo do upload;
    erros do Pydantic recebem mensagem genérica para não refletir seus dados.
    """
    try:
        content = await file.read(MAX_BYTES + 1)
        table = read_table(content, file.filename or "", delimiter, encoding, sheet)
        config = Rules.model_validate(json.loads(rules))
        return audit_table(table, config).to_dict()
    except (ValueError, ValidationError) as error:
        # Mensagem de validação Pydantic pode conter entradas originais: não refletir o corpo.
        message = "Configuração de regras inválida." if isinstance(error, ValidationError) else str(error)
        raise HTTPException(status_code=422, detail=message) from error
    finally:
        await file.close()
