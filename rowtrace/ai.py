"""Explicação opcional via Responses API usando apenas contagens agregadas."""
import json
import os

from pydantic import BaseModel, ConfigDict, Field

from .engine import Audit
from .reports import explain_local


# Contrato de uma prioridade gerada: ID da regra, explicação e ação sugerida.
class Priority(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str
    explanation: str = Field(min_length=1, max_length=1200)
    action: str = Field(min_length=1, max_length=1200)


# Saída estruturada: resumo e até sete prioridades. IDs são conferidos após a resposta.
class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overview: str = Field(min_length=1, max_length=1600)
    priorities: list[Priority] = Field(max_length=7)


def explain_ai(audit: Audit, approved=False, client=None, model=None):
    """Organiza prioridades a partir de contagens agregadas com consentimento explícito.

    Args:
        audit: Auditoria concluída; os registros originais não entram no payload.
        approved: Deve ser True mesmo quando um cliente de teste é utilizado.
        client: Cliente compatível com responses.create, opcional para testes.
        model: Modelo explícito ou valor de OPENAI_MODEL.

    Returns:
        Dicionário overview/priorities. Sem ocorrências, retorna explicação
        local após conferir a autorização, sem chamar o provedor.

    Raises:
        ValueError: Ausência de consentimento, modelo ou chave necessária.
        RuntimeError: Falha externa, JSON inválido ou referência de regra inválida.

    Envia somente rows, affected_rows, findings e by_rule. Não envia nome de
    arquivo, nomes de colunas ou células. Valida formato e IDs, não a correção
    semântica de toda frase. Uma falha não modifica o resultado determinístico.
    """
    if not approved:
        raise ValueError("Autorize o envio das contagens agregadas antes de usar IA.")
    if not audit.findings:
        return {"overview": explain_local(audit), "priorities": []}
    selected_model = model or os.environ.get("OPENAI_MODEL")
    if not selected_model:
        raise ValueError("Configure OPENAI_MODEL para habilitar a explicação com IA.")
    if client is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("Configure OPENAI_API_KEY no ambiente do servidor.")
        from openai import OpenAI
        client = OpenAI(timeout=25, max_retries=1)
    # Lista fechada de agregados: impede incluir acidentalmente células e cabeçalhos.
    payload = {key: audit.summary[key] for key in ["rows", "affected_rows", "findings", "by_rule"]}
    try:
        response = client.responses.create(
            model=selected_model, store=False, max_output_tokens=1600,
            instructions=("Explique em português um relatório determinístico de qualidade de dados. "
                          "Use apenas as contagens agregadas fornecidas. Não invente causas, economia de tempo, "
                          "impacto financeiro ou valores de células. Não existe acesso aos registros originais. "
                          "Priorize as regras com ocorrências e sugira verificações humanas na origem. "
                          "Cada regra pode aparecer no máximo uma vez. A IA não altera nem valida os dados."),
            input=json.dumps(payload, ensure_ascii=False),
            text={"format": {"type": "json_schema", "name": "quality_explanation", "strict": True,
                             "schema": Explanation.model_json_schema()}},
        )
        parsed = Explanation.model_validate_json(response.output_text)
        rules = [item.rule for item in parsed.priorities]
        if len(set(rules)) != len(rules) or not set(rules).issubset(payload["by_rule"]):
            raise ValueError("Referência a regra inexistente ou repetida.")
        return parsed.model_dump()
    except Exception as error:
        raise RuntimeError("A explicação com IA não ficou disponível. O relatório local continua pronto.") from error
