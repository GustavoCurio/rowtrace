# RowTrace — qualidade de dados antes da análise

Uma planilha pode abrir normalmente e ainda conter pedidos duplicados, identificadores repetidos, valores negativos ou datas impossíveis. Esses problemas afetam os indicadores derivados da base. O RowTrace automatiza verificações configuráveis e entrega evidência para cada ocorrência.

## Fluxo do produto

1. A pessoa escolhe a demonstração ou envia um CSV/XLSX.
2. Define as regras de acordo com o significado das colunas.
3. Executa a validação e consulta o diagnóstico.
4. Filtra as evidências por problema e identifica os registros afetados.
5. Exporta o relatório visual, as ocorrências ou o resultado estruturado.

A base original é preservada. O produto sugere verificações na origem porque corrigir um identificador, excluir um pedido ou inverter o sinal de um valor depende da regra de negócio.

## Responsabilidades

| Componente | Responsabilidade |
|---|---|
| `rowtrace/io.py` | Leitura, limites, codificação e rejeição de fórmulas |
| `rowtrace/engine.py` | Configuração validada, regras e evidências |
| `rowtrace/reports.py` | Explicação local e exportações |
| `rowtrace/ai.py` | Explicação generativa opcional a partir de agregados |
| `app.py` | Interface, seleção de regras e isolamento por sessão |
| `rowtrace/api.py` | Integração por requisição multipart |
| `rowtrace/__main__.py` | Execução em lote |
| `benchmark.py` | Geração sintética e medição reproduzível |

## Decisões técnicas

**Regras explícitas.** Não se presume que toda coluna numérica deva ser positiva ou que toda data seja obrigatória. A pessoa configura os critérios. A configuração pode ser exportada e reutilizada na linha de comando ou na API.

**SQL com rastreabilidade.** A tabela é carregada em um SQLite temporário em memória. Funções de janela encontram grupos de registros idênticos; agrupamento e seleção encontram identificadores repetidos. Os nomes das colunas recebidas são convertidos para nomes internos controlados antes de compor consultas. Nenhum SQL enviado pelo usuário é executado.

**Python para formatos e relações.** Números são convertidos com `Decimal` e gramática explícita para evitar aceitar separadores malformados ou valores não finitos. Datas são verificadas contra o calendário e o formato configurado. A sequência compara datas válidas; datas vazias ou inválidas não geram uma segunda ocorrência de sequência.

**Referência à origem.** Cada ocorrência registra posição lógica, regra, severidade, coluna, valor, mensagem e sugestão. Duplicatas sinalizam todos os participantes do grupo. Um registro com múltiplos problemas conta uma única vez no total de registros afetados.

**Planilhas sem fórmulas.** A leitura XLSX é feita em modo somente leitura, com links externos desabilitados e sem executar fórmulas. Uma aba com fórmulas é recusada para evitar uma validação enganosa de valores armazenados em cache. Datas e números nativos são reconhecidos sem depender do formato textual selecionado.

**IA limitada à explicação.** A detecção é determinística. O modelo recebe apenas contagens de quatro campos agregados: `rows`, `affected_rows`, `findings` e `by_rule`. A chamada depende de autorização da pessoa na interface. A resposta usa schema estruturado e não pode referenciar identificadores de regras inexistentes ou repetidos. Essa validação de formato não garante a correção de todas as afirmações em linguagem natural.

**Exportações.** O HTML escapa conteúdo recebido para evitar execução como marcação. O CSV neutraliza textos que podem iniciar fórmulas em leitores de planilha. O JSON preserva os valores originais para integrações. O relatório local não depende da disponibilidade do provedor de IA.

## Evidência de funcionamento

A demonstração inclui dez pedidos fictícios e sete tipos de problema. Os testes verificam a lista exata de posições e regras esperadas: nove ocorrências em sete registros. Também verificam dados válidos, calendário, formatação regional, chaves com zeros à esquerda, cabeçalhos incomuns, arquivos inválidos e fluxo da interface.

O benchmark usa bases sintéticas de 1.000, 10.000 e 50.000 registros. A cada grupo de cem registros, cinco posições recebem, respectivamente, valor negativo, campo vazio, número inválido, data impossível e sequência de datas invertida. A quantidade esperada é calculada a partir das posições injetadas e comparada ao resultado do motor em cada execução.

O teste de 50.000 registros encontrou as 2.500 ocorrências esperadas. A mediana local de leitura foi 108,98 ms; a validação levou 1.058,87 ms. Os números representam esse conjunto sintético e esse ambiente, sem medir interface, exportação, chamadas de IA ou trabalho humano.

A suíte possui 45 testes. A integração do provedor é verificada com cliente simulado; a geração com uma chave real não faz parte dos resultados publicados.

## Escopo operacional

O sistema oferece verificações tabulares configuráveis. Detecção estatística de anomalias, regras entre várias tabelas, conexões a bancos externos e correção automática não fazem parte desta versão. Novas regras de negócio precisam ser implementadas e avaliadas explicitamente.

O servidor local não oferece autenticação ou uso compartilhado com controle de acesso. Para hospedagem externa, são necessários autenticação, limites de requisição/carga, política de retenção e avaliação dos dados que serão processados. A opção de IA é desacoplada e pode permanecer desabilitada.

## Referências

- [pandas: preservação dos tipos na leitura de planilhas](https://pandas.pydata.org/docs/reference/api/pandas.read_excel.html)
- [openpyxl: leitura de XLSX e proteção XML](https://openpyxl.readthedocs.io/en/stable/)
- [FastAPI: arquivos enviados em requisições](https://fastapi.tiangolo.com/tutorial/request-files/)
- [OpenAI: saídas estruturadas na Responses API](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
