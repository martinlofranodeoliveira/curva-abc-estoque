# Formato de Entrada e Pacote de Saída

O **Curva ABC de Estoque** (`1.0.0`) processa um documento JSON (`schema_version: 1`) e publica um pacote ZIP determinístico com oito arquivos.

## 1. Regras do Codec JSON Estrito

- Codificação **UTF-8 estrita sem BOM** (`\ufeff` é recusado com `JSON_BOM`).
- **Proibição de `float` e constantes não finitas**: qualquer número com ponto decimal, notação exponencial, `NaN`, `Infinity` ou `-Infinity` é recusado imediatamente (`JSON_FLOAT` / `JSON_INVALID`).
- **Proibição de chaves duplicadas**: objetos com chaves repetidas são recusados com `JSON_DUPLICATE_KEY`.
- **Proibição de `bool` como inteiro**: `true` e `false` não são aceitos em campos numéricos.
- **Esquema fechado**: qualquer chave desconhecida ou ausente em qualquer nível recusa o documento com `INPUT_SCHEMA`.
- **Limites de leitura**: até `8.388.608` bytes (8 MiB), profundidade máxima `16` e até `250.000` nós JSON na entrada (`64 MiB` e `1.000.000` nós nos documentos derivados).

## 2. Esquema de Entrada (`schema_version: 1`)

```json
{
  "schema_version": 1,
  "inventario": {
    "id": "almox-eletromec-2026-q1",
    "titulo": "Almoxarifado de Manutenção Eletromecânica — Oficina Central Q1",
    "data_referencia": "2026-03-31",
    "moeda": "BRL"
  },
  "parametros": {
    "dias_analise": 90,
    "limite_a_racional": "4/5",
    "limite_ab_racional": "19/20",
    "dias_seguranca_padrao": 6
  },
  "itens": [
    {
      "codigo": "INV-001",
      "descricao": "Inversor de Frequência Trifásico 7,5 cv 380 V",
      "categoria": "Acionamentos",
      "unidade": "UN",
      "custo_unitario_centavos": 140000,
      "consumo_periodo": 5,
      "estoque_atual": 1,
      "estoque_em_transito": 0,
      "lead_time_dias": 18,
      "dias_seguranca": 9,
      "lote_minimo": 1
    }
  ]
}
```

### Limites e domínios dos campos

| Caminho | Tipo | Restrições |
| --- | --- | --- |
| `schema_version` | `int` | Exatamente `1` |
| `inventario.id` | `str` | Identificador ASCII `[A-Za-z0-9][A-Za-z0-9_.-]{0,79}` |
| `inventario.titulo` | `str` | 1 a 160 caracteres, sem controles ou espaços nas extremidades |
| `inventario.data_referencia` | `str` | Data ISO `YYYY-MM-DD` válida de calendário |
| `inventario.moeda` | `str` | Código ISO de 3 letras maiúsculas (ex.: `"BRL"`) |
| `parametros.dias_analise` | `int` | `1..3660` |
| `parametros.limite_a_racional` | `str` | Fração racional `"p/q"` com `0 < limite_a < limite_ab < 1` |
| `parametros.limite_ab_racional` | `str` | Fração racional `"p/q"` com `0 < limite_a < limite_ab < 1` |
| `parametros.dias_seguranca_padrao` | `int` | `0..365` |
| `itens` | `list` | 1 a 5.000 itens com `codigo` ASCII único e soma de consumo > 0 |
| `itens[].codigo` | `str` | Identificador ASCII `[A-Za-z0-9][A-Za-z0-9_.-]{0,79}` único |
| `itens[].descricao` | `str` | 1 a 160 caracteres sem controles ou espaços extremos |
| `itens[].categoria` | `str` | 1 a 80 caracteres sem controles ou espaços extremos |
| `itens[].unidade` | `str` | 1 a 16 caracteres (ex.: `"UN"`, `"BAL"`, `"CTO"`, `"PCT"`) |
| `itens[].custo_unitario_centavos` | `int` | `1..1000000000` (centavos inteiros estritamente positivos) |
| `itens[].consumo_periodo` | `int` | `0..1000000000` |
| `itens[].estoque_atual` | `int` | `0..1000000000` |
| `itens[].estoque_em_transito` | `int` (opcional) | `0..1000000000` (padrão `0` quando omitido) |
| `itens[].lead_time_dias` | `int` | `1..365` |
| `itens[].dias_seguranca` | `int` (opcional) | `0..365` (quando omitido, herda `parametros.dias_seguranca_padrao`) |
| `itens[].lote_minimo` | `int` | `1..1000000` |

## 3. Conteúdo do Pacote ZIP

Os oito arquivos do pacote têm timestamp fixo `1980-01-01 00:00:00`, permissão `0644` e compressão `ZIP_STORED`.

1. `LEIA-ME.md`: instruções do pacote e escopo técnico.
2. `RELATORIO.md`: relatório em Markdown com resumo por classe ABC, indicadores de reposição e memória de cálculo.
3. `SHA256SUMS`: lista `<sha256>  <arquivo>` dos outros sete membros do ZIP.
4. `analise.json`: documento estruturado com `inventario`, `parametros`, `resumo_abc`, `resumo_geral` e `itens`.
5. `curva-abc.csv`: tabela ordenada de Pareto com valores em centavos, frações racionais, classes e flags de empate.
6. `curva-pareto.svg`: painel visual `1440×900` com barras de participação, curva acumulada e tabela de cobertura.
7. `entrada.json`: cópia byte a byte do arquivo de entrada.
8. `reposicao.csv`: tabela operacional com estoque disponível, ponto de pedido, cobertura, status e pedido sugerido.

Nos arquivos CSV, qualquer célula textual cujo conteúdo (após espaços iniciais) comece com `=`, `+`, `-`, `@`, `\t` ou `\r` recebe um apóstrofo inicial (`'`) para evitar execução de fórmulas ao abrir em editores de planilhas.
