# API Python e Modelo Matemático

```python
from curva_abc_estoque import analisar, analisar_json, build_report, write_report
from curva_abc_estoque.jsoncodec import loads

documento = loads(json_bytes)
resultado = analisar(documento)
relatorio_dict = resultado.to_dict()
frac_part = resultado.share_fraction("INV-001")
frac_acum = resultado.cumulative_share_fraction("DIS-004")
frac_cob = resultado.coverage_days_fraction("ROL-002")
```

## Funções públicas

- `analisar(document: object) -> InventoryResult`: valida integralmente o dicionário de entrada (inclusive quando construído em memória) e retorna um `InventoryResult` imutável.
- `analisar_json(data: bytes | str) -> InventoryResult`: decodifica bytes ou texto UTF-8 pelo codec JSON estrito e invoca `analisar`.
- `build_report(result: InventoryResult, input_bytes: bytes) -> bytes`: gera e verifica em memória os bytes do pacote ZIP determinístico, garantindo que `input_bytes` corresponda estruturalmente ao documento analisado.
- `write_report(path: str | os.PathLike, result: InventoryResult, input_bytes: bytes) -> Path`: publica o pacote ZIP de forma atômica e exclusiva via `os.link`, recusando sobrescrever destinos existentes.

## Imutabilidade de `InventoryResult`

- `result.to_dict()` devolve uma nova árvore de dicionários e listas a cada chamada.
- `result.source_document()` devolve uma nova cópia do documento de entrada validado.
- `result.share_fraction(codigo)` devolve um `fractions.Fraction` exato com a participação individual do item no valor total de consumo.
- `result.cumulative_share_fraction(codigo)` devolve um `fractions.Fraction` exato com a participação acumulada de Pareto até o item.
- `result.coverage_days_fraction(codigo)` devolve um `fractions.Fraction` exato com a cobertura em dias do saldo disponível (`None` quando `consumo_periodo == 0`).

## Fórmulas exatas por item

Para cada item após a ordenação determinística por `(-valor_consumo_centavos, -custo_unitario_centavos, codigo)`:

1. **Valor de consumo e participação racional**:
   - `valor_consumo_centavos = consumo_periodo * custo_unitario_centavos`
   - `participacao = Fraction(valor_consumo_centavos, valor_consumo_total_centavos)`
   - `participacao_acumulada = Fraction(valor_consumo_acumulado_centavos, valor_consumo_total_centavos)`
   - Invariante global: `sum(participacao) == Fraction(1, 1)`.
2. **Classificação ABC e detecção de empates**:
   - Se `valor_consumo_centavos == 0`, `classe_abc = "C"`.
   - Caso contrário, `classe_abc = "A"` se `participacao_acumulada <= limite_a` ou `posicao_pareto == 1`; `"B"` se `limite_a < participacao_acumulada <= limite_ab`; `"C"` se `participacao_acumulada > limite_ab`.
   - `empate_valor = True` quando mais de um item possui o mesmo `valor_consumo_centavos`.
   - `empate_fronteira = True` quando itens com o mesmo `valor_consumo_centavos` recebem classes ABC distintas por atravessarem `limite_a` ou `limite_ab`.
3. **Estoque de segurança, ponto de pedido, cobertura e lote mínimo**:
   - `estoque_disponivel = estoque_atual + estoque_em_transito`
   - `demanda_media_diaria = Fraction(consumo_periodo, dias_analise)`
   - `demanda_lead_time = ceil(Fraction(consumo_periodo * lead_time_dias, dias_analise))`
   - `estoque_seguranca = ceil(Fraction(consumo_periodo * dias_seguranca, dias_analise))`
   - `ponto_pedido = demanda_lead_time + estoque_seguranca`
   - `cobertura_dias = Fraction(estoque_disponivel * dias_analise, consumo_periodo)` quando `consumo_periodo > 0`, ou `None` quando `consumo_periodo == 0`.
   - `status_reposicao`:
     - `"sem_consumo"` quando `consumo_periodo == 0`
     - `"critico"` quando `consumo_periodo > 0` e `estoque_disponivel <= estoque_seguranca`
     - `"repor"` quando `consumo_periodo > 0` e `estoque_seguranca < estoque_disponivel < ponto_pedido`
     - `"adequado"` quando `consumo_periodo > 0` e `estoque_disponivel >= ponto_pedido`
   - Para `"critico"` e `"repor"`:
     - `deficit_unidades = ponto_pedido - estoque_disponivel`
     - `lotes_sugeridos = (deficit_unidades + lote_minimo - 1) // lote_minimo`
     - `pedido_sugerido_unidades = lotes_sugeridos * lote_minimo`
     - `valor_pedido_sugerido_centavos = pedido_sugerido_unidades * custo_unitario_centavos`

## Códigos de erro (`EstoqueError`)

`EstoqueError` herda de `ValueError` e expõe `code`, `message` e `path`.

| Código | Condição |
| --- | --- |
| `JSON_INVALID` | Sintaxe JSON inválida, UTF-8 inválido ou constante não finita |
| `JSON_BOM` | Presença de UTF-8 BOM (`\ufeff`) |
| `JSON_FLOAT` | Número com ponto decimal ou expoente |
| `JSON_DUPLICATE_KEY` | Chave repetida no mesmo objeto JSON |
| `JSON_LIMIT` | Excesso de bytes, nós, profundidade ou dígitos inteiros |
| `INPUT_SCHEMA` | Campo obrigatório ausente, chave desconhecida ou tipo inválido |
| `INPUT_ID` | Identificador fora do padrão ASCII ou código duplicado |
| `INPUT_LIMIT` | Quantidade ou valor inteiro fora da faixa permitida |
| `INPUT_DATE` | Data ISO `YYYY-MM-DD` inválida |
| `INPUT_RATIONAL` | Fração racional fora do formato `"p/q"` ou limites ABC inválidos |
| `DOMAIN_ZERO_TOTAL` | Valor total de consumo do inventário igual a zero |
| `OUTPUT_EXISTS` | Arquivo ou symlink de destino já existe |
| `OUTPUT_PARENT` | Diretório pai da saída não existe |
