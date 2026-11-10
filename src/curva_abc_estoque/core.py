"""Motor determinístico de classificação ABC de Pareto, cobertura e ponto de pedido."""
from __future__ import annotations

from datetime import date
from fractions import Fraction
import re

from .errors import EstoqueError
from .jsoncodec import dumps

SCHEMA_VERSION = 1

MAX_ITENS = 5_000
MAX_DIAS_ANALISE = 3_660
MAX_LEAD_TIME_DIAS = 365
MAX_DIAS_SEGURANCA = 365
MAX_CUSTO_CENTAVOS = 1_000_000_000
MAX_QUANTIDADE = 1_000_000_000
MAX_LOTE_MINIMO = 1_000_000

CLASSES_ABC = ("A", "B", "C")
STATUS_REPOSICAO = ("critico", "repor", "adequado", "sem_consumo")

_ASCII_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
_CURRENCY_RE = re.compile(r"[A-Z]{3}\Z")
_DATE_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})\Z")
_RATIONAL_RE = re.compile(r"([0-9]{1,20})/([1-9][0-9]{0,19})\Z")


def _err(code: str, message: str, path: str = "$") -> EstoqueError:
    return EstoqueError(code, message, path)


def _clone(value: object) -> object:
    if type(value) is dict:
        return {k: _clone(v) for k, v in value.items()}
    if type(value) is list:
        return [_clone(v) for v in value]
    return value


def _require_dict(
    value: object,
    expected_keys: set[str],
    path: str,
    optional_keys: set[str] | None = None,
) -> dict[str, object]:
    if type(value) is not dict:
        raise _err("INPUT_SCHEMA", "Esperado objeto JSON na estrutura.", path)
    actual = set(value.keys())
    allowed = expected_keys | (optional_keys or set())
    if not expected_keys.issubset(actual) or not actual.issubset(allowed):
        raise _err("INPUT_SCHEMA", "Campos obrigatórios ausentes ou chaves desconhecidas no objeto.", path)
    return value


def _require_int(value: object, minimum: int, maximum: int, path: str, code: str = "INPUT_LIMIT") -> int:
    if type(value) is not int:
        raise _err("INPUT_SCHEMA", "Campo numérico exige inteiro estrito (sem bool ou float).", path)
    if not (minimum <= value <= maximum):
        raise _err(code, "Valor inteiro fora do intervalo permitido.", path)
    return value


def _require_clean_text(value: object, min_len: int, max_len: int, path: str) -> str:
    if type(value) is not str:
        raise _err("INPUT_SCHEMA", "Campo textual exige string.", path)
    if not (min_len <= len(value) <= max_len):
        raise _err("INPUT_LIMIT", "Comprimento de texto fora dos limites permitidos.", path)
    if value and (value != value.strip()):
        raise _err("INPUT_SCHEMA", "Texto não pode iniciar ou terminar com espaços.", path)
    for ch in value:
        codepoint = ord(ch)
        if codepoint < 32 or codepoint == 127 or (0xD800 <= codepoint <= 0xDFFF) or codepoint in (0x2028, 0x2029):
            raise _err("INPUT_SCHEMA", "Texto contém caractere de controle ou separador inválido.", path)
    return value


def _require_ascii_id(value: object, path: str) -> str:
    if type(value) is not str or _ASCII_ID.fullmatch(value) is None:
        raise _err("INPUT_ID", "Identificador deve ser ASCII estrito de 1 a 80 caracteres.", path)
    return value


def _parse_date(value: object, path: str) -> tuple[str, date]:
    if type(value) is not str or _DATE_RE.fullmatch(value) is None:
        raise _err("INPUT_DATE", "Data deve seguir estritamente o formato YYYY-MM-DD.", path)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise _err("INPUT_DATE", "Data de calendário inexistente.", path) from None
    return value, parsed


def _parse_rational(value: object, path: str) -> Fraction:
    if type(value) is not str:
        raise _err("INPUT_RATIONAL", "Fração racional deve ser texto no formato 'p/q'.", path)
    match = _RATIONAL_RE.fullmatch(value)
    if match is None:
        raise _err("INPUT_RATIONAL", "Fração racional deve seguir estritamente o formato 'p/q'.", path)
    num = int(match[1])
    den = int(match[2])
    if num > 0 and match[1].startswith("0"):
        raise _err("INPUT_RATIONAL", "Numerador racional não pode conter zeros à esquerda.", path)
    if num == 0 and match[1] != "0":
        raise _err("INPUT_RATIONAL", "Zero racional deve usar numerador '0'.", path)
    return Fraction(num, den)


def _rational_str(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def _ceil_fraction(value: Fraction) -> int:
    if value <= 0:
        return 0
    return (value.numerator + value.denominator - 1) // value.denominator


def _bps_from_fraction(value: Fraction) -> int:
    """Converte fração exata em pontos-base inteiros (0..10000) com arredondamento half-up."""
    scaled = value * 10_000
    return (2 * scaled.numerator + scaled.denominator) // (2 * scaled.denominator)


class InventoryResult:
    """Resultado imutável da análise ABC de Pareto, cobertura e ponto de pedido."""

    def __init__(self, source_doc: dict[str, object], report_doc: dict[str, object]) -> None:
        self._source = _clone(source_doc)
        self._report = _clone(report_doc)

    def to_dict(self) -> dict[str, object]:
        """Retorna um novo dicionário com o resultado completo da análise."""
        return _clone(self._report)  # type: ignore[return-value]

    def source_document(self) -> dict[str, object]:
        """Retorna um novo dicionário com o documento de entrada validado."""
        return _clone(self._source)  # type: ignore[return-value]

    def share_fraction(self, codigo: str) -> Fraction:
        """Retorna a participação individual exata no valor de consumo como Fraction."""
        for item in self._report["itens"]:  # type: ignore[index]
            if item["codigo"] == codigo:
                num, den = item["participacao_racional"].split("/")
                return Fraction(int(num), int(den))
        raise _err("INPUT_ID", "Código de item solicitado não consta na análise.", "itens")

    def cumulative_share_fraction(self, codigo: str) -> Fraction:
        """Retorna a participação acumulada exata de Pareto como Fraction."""
        for item in self._report["itens"]:  # type: ignore[index]
            if item["codigo"] == codigo:
                num, den = item["participacao_acumulada_racional"].split("/")
                return Fraction(int(num), int(den))
        raise _err("INPUT_ID", "Código de item solicitado não consta na análise.", "itens")

    def coverage_days_fraction(self, codigo: str) -> Fraction | None:
        """Retorna a cobertura exata em dias como Fraction, ou None para item sem consumo."""
        for item in self._report["itens"]:  # type: ignore[index]
            if item["codigo"] == codigo:
                raw = item["cobertura_dias_racional"]
                if raw is None:
                    return None
                num, den = raw.split("/")
                return Fraction(int(num), int(den))
        raise _err("INPUT_ID", "Código de item solicitado não consta na análise.", "itens")


def analisar(document: object) -> InventoryResult:
    """Valida a entrada e calcula classificação ABC de Pareto, cobertura e reposição com frações exatas."""
    dumps(document)
    source = _clone(document)
    root = _require_dict(source, {"schema_version", "inventario", "parametros", "itens"}, "$")

    version = _require_int(root["schema_version"], SCHEMA_VERSION, SCHEMA_VERSION, "$.schema_version", "INPUT_SCHEMA")

    inv_raw = _require_dict(root["inventario"], {"id", "titulo", "data_referencia", "moeda"}, "$.inventario")
    inv_id = _require_ascii_id(inv_raw["id"], "$.inventario.id")
    inv_titulo = _require_clean_text(inv_raw["titulo"], 1, 160, "$.inventario.titulo")
    data_ref_str, _ = _parse_date(inv_raw["data_referencia"], "$.inventario.data_referencia")
    moeda = inv_raw["moeda"]
    if type(moeda) is not str or _CURRENCY_RE.fullmatch(moeda) is None:
        raise _err("INPUT_SCHEMA", "Moeda deve ser código ISO de 3 letras maiúsculas (ex.: BRL).", "$.inventario.moeda")

    param_raw = _require_dict(
        root["parametros"],
        {"dias_analise", "limite_a_racional", "limite_ab_racional", "dias_seguranca_padrao"},
        "$.parametros",
    )
    dias_analise = _require_int(param_raw["dias_analise"], 1, MAX_DIAS_ANALISE, "$.parametros.dias_analise")
    limite_a = _parse_rational(param_raw["limite_a_racional"], "$.parametros.limite_a_racional")
    limite_ab = _parse_rational(param_raw["limite_ab_racional"], "$.parametros.limite_ab_racional")
    if not (Fraction(0, 1) < limite_a < limite_ab < Fraction(1, 1)):
        raise _err(
            "INPUT_RATIONAL",
            "Limites ABC exigem 0 < limite_a_racional < limite_ab_racional < 1.",
            "$.parametros",
        )
    dias_seg_padrao = _require_int(
        param_raw["dias_seguranca_padrao"],
        0,
        MAX_DIAS_SEGURANCA,
        "$.parametros.dias_seguranca_padrao",
    )

    itens_raw = root["itens"]
    if type(itens_raw) is not list:
        raise _err("INPUT_SCHEMA", "O campo itens deve ser uma lista JSON.", "$.itens")
    if not (1 <= len(itens_raw) <= MAX_ITENS):
        raise _err("INPUT_LIMIT", "Quantidade de itens fora do limite permitido (1..5000).", "$.itens")

    seen_codigos: set[str] = set()
    parsed_items: list[dict[str, object]] = []
    valor_consumo_total = 0

    for idx, raw_item in enumerate(itens_raw):
        i_path = f"$.itens[{idx}]"
        item = _require_dict(
            raw_item,
            {
                "codigo",
                "descricao",
                "categoria",
                "unidade",
                "custo_unitario_centavos",
                "consumo_periodo",
                "estoque_atual",
                "lead_time_dias",
                "lote_minimo",
            },
            i_path,
            optional_keys={"estoque_em_transito", "dias_seguranca"},
        )
        codigo = _require_ascii_id(item["codigo"], f"{i_path}.codigo")
        if codigo in seen_codigos:
            raise _err("INPUT_ID", "Código de item duplicado no inventário.", f"{i_path}.codigo")
        seen_codigos.add(codigo)

        descricao = _require_clean_text(item["descricao"], 1, 160, f"{i_path}.descricao")
        categoria = _require_clean_text(item["categoria"], 1, 80, f"{i_path}.categoria")
        unidade = _require_clean_text(item["unidade"], 1, 16, f"{i_path}.unidade")

        custo_unit = _require_int(
            item["custo_unitario_centavos"], 1, MAX_CUSTO_CENTAVOS, f"{i_path}.custo_unitario_centavos"
        )
        consumo = _require_int(item["consumo_periodo"], 0, MAX_QUANTIDADE, f"{i_path}.consumo_periodo")
        estoque_atual = _require_int(item["estoque_atual"], 0, MAX_QUANTIDADE, f"{i_path}.estoque_atual")
        if "estoque_em_transito" in item:
            estoque_transito = _require_int(
                item["estoque_em_transito"], 0, MAX_QUANTIDADE, f"{i_path}.estoque_em_transito"
            )
        else:
            estoque_transito = 0

        lead_time = _require_int(item["lead_time_dias"], 1, MAX_LEAD_TIME_DIAS, f"{i_path}.lead_time_dias")
        if "dias_seguranca" in item:
            dias_seg = _require_int(item["dias_seguranca"], 0, MAX_DIAS_SEGURANCA, f"{i_path}.dias_seguranca")
        else:
            dias_seg = dias_seg_padrao

        lote_min = _require_int(item["lote_minimo"], 1, MAX_LOTE_MINIMO, f"{i_path}.lote_minimo")

        valor_consumo = consumo * custo_unit
        valor_consumo_total += valor_consumo

        parsed_items.append(
            {
                "codigo": codigo,
                "descricao": descricao,
                "categoria": categoria,
                "unidade": unidade,
                "custo_unitario_centavos": custo_unit,
                "consumo_periodo": consumo,
                "valor_consumo_centavos": valor_consumo,
                "estoque_atual": estoque_atual,
                "estoque_em_transito": estoque_transito,
                "lead_time_dias": lead_time,
                "dias_seguranca": dias_seg,
                "lote_minimo": lote_min,
            }
        )

    if valor_consumo_total <= 0:
        raise _err(
            "DOMAIN_ZERO_TOTAL",
            "O valor total de consumo do período deve ser estritamente positivo.",
            "$.itens",
        )

    # Ordenação determinística de Pareto: maior valor de consumo, maior custo unitário, código ASCII
    parsed_items.sort(
        key=lambda it: (
            -int(it["valor_consumo_centavos"]),  # type: ignore[arg-type]
            -int(it["custo_unitario_centavos"]),  # type: ignore[arg-type]
            str(it["codigo"]),
        )
    )

    freq_por_valor: dict[int, int] = {}
    for it in parsed_items:
        val = int(it["valor_consumo_centavos"])  # type: ignore[arg-type]
        freq_por_valor[val] = freq_por_valor.get(val, 0) + 1

    acumulado_centavos = 0
    soma_participacoes = Fraction(0, 1)
    classes_por_valor: dict[int, set[str]] = {}
    itens_out: list[dict[str, object]] = []

    for pos, it in enumerate(parsed_items, start=1):
        val_consumo = int(it["valor_consumo_centavos"])  # type: ignore[arg-type]
        custo_unit = int(it["custo_unitario_centavos"])  # type: ignore[arg-type]
        consumo = int(it["consumo_periodo"])  # type: ignore[arg-type]
        est_atual = int(it["estoque_atual"])  # type: ignore[arg-type]
        est_transito = int(it["estoque_em_transito"])  # type: ignore[arg-type]
        est_disp = est_atual + est_transito
        lead_time = int(it["lead_time_dias"])  # type: ignore[arg-type]
        dias_seg = int(it["dias_seguranca"])  # type: ignore[arg-type]
        lote_min = int(it["lote_minimo"])  # type: ignore[arg-type]

        part_anterior = Fraction(acumulado_centavos, valor_consumo_total)
        acumulado_centavos += val_consumo
        part_individual = Fraction(val_consumo, valor_consumo_total)
        part_acumulada = Fraction(acumulado_centavos, valor_consumo_total)
        soma_participacoes += part_individual

        if val_consumo == 0:
            classe = "C"
        elif part_acumulada <= limite_a or pos == 1:
            classe = "A"
        elif part_acumulada <= limite_ab:
            classe = "B"
        else:
            classe = "C"

        classes_por_valor.setdefault(val_consumo, set()).add(classe)

        demanda_diaria_frac = Fraction(consumo, dias_analise)
        demanda_lt_frac = Fraction(consumo * lead_time, dias_analise)
        demanda_lt_int = _ceil_fraction(demanda_lt_frac)
        estoque_seg_frac = Fraction(consumo * dias_seg, dias_analise)
        estoque_seg_int = _ceil_fraction(estoque_seg_frac)
        ponto_pedido_frac = Fraction(consumo * (lead_time + dias_seg), dias_analise)
        ponto_pedido_int = demanda_lt_int + estoque_seg_int

        if consumo > 0:
            cobertura_frac: Fraction | None = Fraction(est_disp * dias_analise, consumo)
            cobertura_str: str | None = _rational_str(cobertura_frac)
            cobertura_int: int | None = (est_disp * dias_analise) // consumo
        else:
            cobertura_frac = None
            cobertura_str = None
            cobertura_int = None

        if consumo == 0:
            status = "sem_consumo"
            deficit = 0
            lotes_sug = 0
            pedido_sug = 0
        elif est_disp <= estoque_seg_int:
            status = "critico"
            deficit = ponto_pedido_int - est_disp
            lotes_sug = (deficit + lote_min - 1) // lote_min
            pedido_sug = lotes_sug * lote_min
        elif est_disp < ponto_pedido_int:
            status = "repor"
            deficit = ponto_pedido_int - est_disp
            lotes_sug = (deficit + lote_min - 1) // lote_min
            pedido_sug = lotes_sug * lote_min
        else:
            status = "adequado"
            deficit = 0
            lotes_sug = 0
            pedido_sug = 0

        val_est_atual = est_atual * custo_unit
        val_est_disp = est_disp * custo_unit
        val_pedido_sug = pedido_sug * custo_unit

        itens_out.append(
            {
                "posicao_pareto": pos,
                "codigo": it["codigo"],
                "descricao": it["descricao"],
                "categoria": it["categoria"],
                "unidade": it["unidade"],
                "custo_unitario_centavos": custo_unit,
                "consumo_periodo": consumo,
                "valor_consumo_centavos": val_consumo,
                "valor_consumo_acumulado_centavos": acumulado_centavos,
                "participacao_racional": _rational_str(part_individual),
                "participacao_anterior_racional": _rational_str(part_anterior),
                "participacao_acumulada_racional": _rational_str(part_acumulada),
                "participacao_bps": _bps_from_fraction(part_individual),
                "participacao_acumulada_bps": _bps_from_fraction(part_acumulada),
                "classe_abc": classe,
                "empate_valor": freq_por_valor[val_consumo] > 1,
                "empate_fronteira": False,  # preenchido após varredura das classes por valor
                "estoque_atual": est_atual,
                "estoque_em_transito": est_transito,
                "estoque_disponivel": est_disp,
                "valor_estoque_atual_centavos": val_est_atual,
                "valor_estoque_disponivel_centavos": val_est_disp,
                "lead_time_dias": lead_time,
                "dias_seguranca": dias_seg,
                "lote_minimo": lote_min,
                "demanda_media_diaria_racional": _rational_str(demanda_diaria_frac),
                "demanda_lead_time_racional": _rational_str(demanda_lt_frac),
                "demanda_lead_time": demanda_lt_int,
                "estoque_seguranca_racional": _rational_str(estoque_seg_frac),
                "estoque_seguranca": estoque_seg_int,
                "ponto_pedido_racional": _rational_str(ponto_pedido_frac),
                "ponto_pedido": ponto_pedido_int,
                "cobertura_dias_racional": cobertura_str,
                "cobertura_dias_inteiro": cobertura_int,
                "status_reposicao": status,
                "deficit_unidades": deficit,
                "lotes_sugeridos": lotes_sug,
                "pedido_sugerido_unidades": pedido_sug,
                "valor_pedido_sugerido_centavos": val_pedido_sug,
            }
        )

    if soma_participacoes != Fraction(1, 1):
        raise _err("DOMAIN_INVARIANT", "A soma das participações racionais não fechou em 1/1.", "$.itens")

    # Marcação de empate_fronteira e consolidação por classe e geral
    total_itens = len(itens_out)
    itens_empate_valor = 0
    itens_empate_fronteira = 0
    contagem_status: dict[str, int] = {s: 0 for s in STATUS_REPOSICAO}

    resumo_classes_acc: dict[str, dict[str, int]] = {
        c: {
            "quantidade_itens": 0,
            "valor_consumo_centavos": 0,
            "valor_estoque_atual_centavos": 0,
            "valor_pedido_sugerido_centavos": 0,
        }
        for c in CLASSES_ABC
    }

    val_est_atual_total = 0
    val_est_disp_total = 0
    val_pedido_total = 0
    unid_pedido_total = 0

    for item_out in itens_out:
        val_c = int(item_out["valor_consumo_centavos"])  # type: ignore[arg-type]
        is_fronteira = len(classes_por_valor[val_c]) > 1
        item_out["empate_fronteira"] = is_fronteira
        if item_out["empate_valor"]:
            itens_empate_valor += 1
        if is_fronteira:
            itens_empate_fronteira += 1

        st = str(item_out["status_reposicao"])
        contagem_status[st] += 1

        cl = str(item_out["classe_abc"])
        acc = resumo_classes_acc[cl]
        acc["quantidade_itens"] += 1
        acc["valor_consumo_centavos"] += val_c
        acc["valor_estoque_atual_centavos"] += int(item_out["valor_estoque_atual_centavos"])  # type: ignore[arg-type]
        acc["valor_pedido_sugerido_centavos"] += int(item_out["valor_pedido_sugerido_centavos"])  # type: ignore[arg-type]

        val_est_atual_total += int(item_out["valor_estoque_atual_centavos"])  # type: ignore[arg-type]
        val_est_disp_total += int(item_out["valor_estoque_disponivel_centavos"])  # type: ignore[arg-type]
        val_pedido_total += int(item_out["valor_pedido_sugerido_centavos"])  # type: ignore[arg-type]
        unid_pedido_total += int(item_out["pedido_sugerido_unidades"])  # type: ignore[arg-type]

    resumo_abc: dict[str, dict[str, object]] = {}
    for cl in CLASSES_ABC:
        acc = resumo_classes_acc[cl]
        part_itens_cl = Fraction(acc["quantidade_itens"], total_itens)
        part_valor_cl = Fraction(acc["valor_consumo_centavos"], valor_consumo_total)
        resumo_abc[cl] = {
            "classe": cl,
            "quantidade_itens": acc["quantidade_itens"],
            "participacao_itens_racional": _rational_str(part_itens_cl),
            "participacao_itens_bps": _bps_from_fraction(part_itens_cl),
            "valor_consumo_centavos": acc["valor_consumo_centavos"],
            "participacao_valor_racional": _rational_str(part_valor_cl),
            "participacao_valor_bps": _bps_from_fraction(part_valor_cl),
            "valor_estoque_atual_centavos": acc["valor_estoque_atual_centavos"],
            "valor_pedido_sugerido_centavos": acc["valor_pedido_sugerido_centavos"],
        }

    report_doc: dict[str, object] = {
        "schema_version": version,
        "motor": "curva-abc-estoque",
        "versao_motor": "1.0.0",
        "inventario": {
            "id": inv_id,
            "titulo": inv_titulo,
            "data_referencia": data_ref_str,
            "moeda": moeda,
        },
        "parametros": {
            "dias_analise": dias_analise,
            "limite_a_racional": _rational_str(limite_a),
            "limite_ab_racional": _rational_str(limite_ab),
            "limite_a_bps": _bps_from_fraction(limite_a),
            "limite_ab_bps": _bps_from_fraction(limite_ab),
            "dias_seguranca_padrao": dias_seg_padrao,
        },
        "resumo_abc": resumo_abc,
        "resumo_geral": {
            "quantidade_itens": total_itens,
            "soma_participacoes_racional": _rational_str(soma_participacoes),
            "valor_consumo_total_centavos": valor_consumo_total,
            "valor_estoque_atual_total_centavos": val_est_atual_total,
            "valor_estoque_disponivel_total_centavos": val_est_disp_total,
            "valor_pedido_sugerido_total_centavos": val_pedido_total,
            "unidades_pedido_sugerido_total": unid_pedido_total,
            "itens_empate_valor": itens_empate_valor,
            "itens_empate_fronteira": itens_empate_fronteira,
            "contagem_status_reposicao": contagem_status,
        },
        "itens": itens_out,
    }

    return InventoryResult(source, report_doc)
