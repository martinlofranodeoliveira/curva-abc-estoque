# Verificação Independente e Interoperabilidade

O verificador `tools/verify_independent.py` audita pacotes ZIP gerados pela CLI sem importar `curva_abc_estoque`, confrontando os resultados com dois oráculos independentes da biblioteca padrão do Python (`fractions.Fraction` e `sqlite3`).

## 1. Oráculo Racional Independente (`fractions.Fraction`)

A partir de `entrada.json`, o verificador recalcula independentemente:
- O valor de consumo em centavos de cada item e a ordenação de Pareto `(-valor_consumo_centavos, -custo_unitario_centavos, codigo)`.
- As frações exatas `participacao_racional` e `participacao_acumulada_racional`, comprovando que a soma de todas as participações fecha exatamente em `Fraction(1, 1)`.
- A atribuição das classes `"A"`, `"B"` e `"C"` frente aos limites racionais `limite_a_racional` e `limite_ab_racional`, bem como as flags `empate_valor` e `empate_fronteira`.
- A demanda no lead time, o estoque de segurança, o ponto de pedido, a cobertura em dias, o status de reposição (`"critico"`, `"repor"`, `"adequado"`, `"sem_consumo"`) e o teto em múltiplos de `lote_minimo`.

## 2. Oráculo Relacional SQLite (`sqlite3`)

O verificador carrega `curva-abc.csv` e `reposicao.csv` em tabelas relacionais em memória no SQLite (`:memory:`), executa `PRAGMA integrity_check` e valida:
- As somas acumuladas de valor de consumo via window function SQL (`SUM(valor_consumo_centavos) OVER (ORDER BY posicao_pareto ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)`).
- Os totais de itens, valor de consumo, estoque atual e valor de pedido sugerido agrupados por `classe_abc` (`GROUP BY classe_abc`).
- A contagem de itens por `status_reposicao` e a igualdade entre os dados dos dois CSVs e `analise.json`.

## 3. Reproduzir a verificação (inclusive sem rede)

Modo autônomo padrão (gera pacotes temporários para o cenário de demonstração e para o cenário sintético de borda e valida ambos):

```sh
python tools/verify_independent.py
```

Contra um pacote ZIP existente:

```sh
python tools/verify_independent.py dist/exemplo-curva-abc.zip --demo
```

Em Linux com suporte a namespaces de usuário e rede isolada (`unshare`):

```sh
unshare --user --map-root-user --net -- python tools/verify_independent.py \
  --require-network-isolation \
  --cli "$(command -v curva-abc-estoque)" \
  --report local/verificacao-sem-rede.json
```
