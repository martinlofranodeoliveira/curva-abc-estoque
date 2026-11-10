"""Geração determinística de relatórios (JSON, CSV, Markdown, SVG e SHA256SUMS) em pacote ZIP."""
from __future__ import annotations

import csv
import hashlib
from html import escape
import io
import json
import os
from pathlib import Path
import tempfile
import zipfile

from .core import InventoryResult, analisar
from .errors import EstoqueError
from .jsoncodec import dumps_output, loads

MAX_BUNDLE_BYTES = 268_435_456

REPORT_FILES = (
    "LEIA-ME.md",
    "RELATORIO.md",
    "SHA256SUMS",
    "analise.json",
    "curva-abc.csv",
    "curva-pareto.svg",
    "entrada.json",
    "reposicao.csv",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def analisar_json(data: bytes | str) -> InventoryResult:
    """Decodifica JSON estrito e executa a análise ABC de Pareto e reposição."""
    return analisar(loads(data))


def _safe_cell(value: str) -> str:
    stripped = value.lstrip(" ")
    if stripped.startswith(_FORMULA_PREFIXES) or value.startswith(("\t", "\r")):
        return "'" + value
    return value


def _xml_text(value: str) -> str:
    if any(ord(ch) in (0xFFFE, 0xFFFF) for ch in value):
        return escape(json.dumps(value, ensure_ascii=True), quote=True)
    return escape(value, quote=True)


def _format_brl(centavos: int) -> str:
    sign = "-" if centavos < 0 else ""
    abs_c = abs(centavos)
    reais, cents = divmod(abs_c, 100)
    reais_str = f"{reais:,}".replace(",", ".")
    return f"{sign}R$ {reais_str},{cents:02d}"


def _format_bps(bps: int) -> str:
    inteiro, frac = divmod(bps, 100)
    return f"{inteiro},{frac:02d}%"


def _curva_abc_csv(report: dict[str, object]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        (
            "posicao_pareto",
            "codigo",
            "descricao",
            "categoria",
            "unidade",
            "custo_unitario_centavos",
            "consumo_periodo",
            "valor_consumo_centavos",
            "valor_consumo_acumulado_centavos",
            "participacao_racional",
            "participacao_acumulada_racional",
            "participacao_bps",
            "participacao_acumulada_bps",
            "classe_abc",
            "empate_valor",
            "empate_fronteira",
        )
    )
    for item in report["itens"]:  # type: ignore[index]
        writer.writerow(
            (
                str(item["posicao_pareto"]),
                _safe_cell(item["codigo"]),
                _safe_cell(item["descricao"]),
                _safe_cell(item["categoria"]),
                _safe_cell(item["unidade"]),
                str(item["custo_unitario_centavos"]),
                str(item["consumo_periodo"]),
                str(item["valor_consumo_centavos"]),
                str(item["valor_consumo_acumulado_centavos"]),
                item["participacao_racional"],
                item["participacao_acumulada_racional"],
                str(item["participacao_bps"]),
                str(item["participacao_acumulada_bps"]),
                item["classe_abc"],
                "true" if item["empate_valor"] else "false",
                "true" if item["empate_fronteira"] else "false",
            )
        )
    return stream.getvalue().encode("utf-8")


def _reposicao_csv(report: dict[str, object]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        (
            "posicao_pareto",
            "codigo",
            "descricao",
            "classe_abc",
            "estoque_atual",
            "estoque_em_transito",
            "estoque_disponivel",
            "lead_time_dias",
            "dias_seguranca",
            "demanda_media_diaria_racional",
            "demanda_lead_time",
            "estoque_seguranca",
            "ponto_pedido",
            "cobertura_dias_racional",
            "cobertura_dias_inteiro",
            "status_reposicao",
            "deficit_unidades",
            "lote_minimo",
            "lotes_sugeridos",
            "pedido_sugerido_unidades",
            "valor_pedido_sugerido_centavos",
        )
    )
    for item in report["itens"]:  # type: ignore[index]
        cob_rac = "" if item["cobertura_dias_racional"] is None else item["cobertura_dias_racional"]
        cob_int = "" if item["cobertura_dias_inteiro"] is None else str(item["cobertura_dias_inteiro"])
        writer.writerow(
            (
                str(item["posicao_pareto"]),
                _safe_cell(item["codigo"]),
                _safe_cell(item["descricao"]),
                item["classe_abc"],
                str(item["estoque_atual"]),
                str(item["estoque_em_transito"]),
                str(item["estoque_disponivel"]),
                str(item["lead_time_dias"]),
                str(item["dias_seguranca"]),
                item["demanda_media_diaria_racional"],
                str(item["demanda_lead_time"]),
                str(item["estoque_seguranca"]),
                str(item["ponto_pedido"]),
                cob_rac,
                cob_int,
                item["status_reposicao"],
                str(item["deficit_unidades"]),
                str(item["lote_minimo"]),
                str(item["lotes_sugeridos"]),
                str(item["pedido_sugerido_unidades"]),
                str(item["valor_pedido_sugerido_centavos"]),
            )
        )
    return stream.getvalue().encode("utf-8")


def _relatorio_md(report: dict[str, object]) -> bytes:
    inv = report["inventario"]  # type: ignore[index]
    param = report["parametros"]  # type: ignore[index]
    res_abc = report["resumo_abc"]  # type: ignore[index]
    geral = report["resumo_geral"]  # type: ignore[index]
    itens = report["itens"]  # type: ignore[index]
    status_cnt = geral["contagem_status_reposicao"]

    lines = [
        f"# Relatório de Curva ABC, Cobertura e Ponto de Pedido — {inv['titulo']}",
        "",
        "> **Aviso de escopo técnico:** Este relatório é gerado por um motor determinístico de cálculo",
        "> exato em centavos inteiros e frações racionais (`fractions.Fraction`) sobre os saldos, consumos,",
        "> prazos de entrega e lotes mínimos declarados na entrada. Não substitui auditoria contábil,",
        "> inventário físico in loco, previsão estatística de demanda nem decisão gerencial de compras.",
        "",
        "## 1. Identificação do Inventário e Parâmetros",
        "",
        f"- **Identificador:** `{inv['id']}`",
        f"- **Data de referência:** `{inv['data_referencia']}`",
        f"- **Moeda:** `{inv['moeda']}`",
        f"- **Janela de análise de consumo:** `{param['dias_analise']} dias`",
        f"- **Fronteira acumulada Classe A:** `{param['limite_a_racional']}` ({_format_bps(param['limite_a_bps'])})",
        f"- **Fronteira acumulada Classes A+B:** `{param['limite_ab_racional']}` ({_format_bps(param['limite_ab_bps'])})",
        f"- **Margem padrão de segurança:** `{param['dias_seguranca_padrao']} dias`",
        "",
        "## 2. Resumo Consolidado por Classe ABC",
        "",
        "| Classe | Itens | % Itens (Racional) | Valor de Consumo | % Valor (Racional) | % Valor | Estoque Atual | Pedido Sugerido |",
        "| :---: | ---: | :---: | ---: | :---: | ---: | ---: | ---: |",
    ]

    for cl in ("A", "B", "C"):
        c_row = res_abc[cl]
        lines.append(
            f"| **{cl}** | {c_row['quantidade_itens']} | `{c_row['participacao_itens_racional']}` "
            f"| {_format_brl(c_row['valor_consumo_centavos'])} | `{c_row['participacao_valor_racional']}` "
            f"| {_format_bps(c_row['participacao_valor_bps'])} "
            f"| {_format_brl(c_row['valor_estoque_atual_centavos'])} "
            f"| {_format_brl(c_row['valor_pedido_sugerido_centavos'])} |"
        )

    lines.extend(
        [
            f"| **Total** | **{geral['quantidade_itens']}** | `1/1` "
            f"| **{_format_brl(geral['valor_consumo_total_centavos'])}** | `{geral['soma_participacoes_racional']}` "
            f"| **100,00%** "
            f"| **{_format_brl(geral['valor_estoque_atual_total_centavos'])}** "
            f"| **{_format_brl(geral['valor_pedido_sugerido_total_centavos'])}** |",
            "",
            "## 3. Indicadores de Reposição e Empates de Fronteira",
            "",
            f"- **Itens em estado crítico (`critico`):** `{status_cnt['critico']}`",
            f"- **Itens em ponto de reposição (`repor`):** `{status_cnt['repor']}`",
            f"- **Itens com cobertura adequada (`adequado`):** `{status_cnt['adequado']}`",
            f"- **Itens sem consumo no período (`sem_consumo`):** `{status_cnt['sem_consumo']}`",
            f"- **Itens com empate exato de valor de consumo (`empate_valor`):** `{geral['itens_empate_valor']}`",
            f"- **Itens em empate sobre a fronteira de classe (`empate_fronteira`):** `{geral['itens_empate_fronteira']}`",
            f"- **Unidades totais sugeridas para compra:** `{geral['unidades_pedido_sugerido_total']}`",
            "",
            "## 4. Memória de Cálculo — Classificação ABC de Pareto",
            "",
            "| Pos. | Código | Descrição | Consumo | Custo Unit. | Valor Consumo | Part. (`p/q`) | Acum. (`p/q`) | Acum. % | Classe | Empate |",
            "| ---: | --- | --- | ---: | ---: | ---: | :---: | :---: | ---: | :---: | :---: |",
        ]
    )

    for item in itens:
        if item["empate_fronteira"]:
            empate_txt = "FRONTEIRA"
        elif item["empate_valor"]:
            empate_txt = "VALOR"
        else:
            empate_txt = "—"
        lines.append(
            f"| {item['posicao_pareto']} | `{item['codigo']}` | {item['descricao']} "
            f"| {item['consumo_periodo']} {item['unidade']} | {_format_brl(item['custo_unitario_centavos'])} "
            f"| {_format_brl(item['valor_consumo_centavos'])} | `{item['participacao_racional']}` "
            f"| `{item['participacao_acumulada_racional']}` | {_format_bps(item['participacao_acumulada_bps'])} "
            f"| **{item['classe_abc']}** | {empate_txt} |"
        )

    lines.extend(
        [
            "",
            "## 5. Memória de Cálculo — Cobertura, Ponto de Pedido e Lote Mínimo",
            "",
            "| Código | Classe | Disp. (Atual+Trâns.) | Dem. Diária | Est. Seg. | Ponto Pedido | Cobertura (dias) | Status | Déficit | Lote Mín. | Pedido Sugerido | Valor Pedido |",
            "| --- | :---: | ---: | :---: | ---: | ---: | :---: | :---: | ---: | ---: | ---: | ---: |",
        ]
    )

    for item in itens:
        cob_str = (
            "—"
            if item["cobertura_dias_racional"] is None
            else f"`{item['cobertura_dias_racional']}` ({item['cobertura_dias_inteiro']}d)"
        )
        lines.append(
            f"| `{item['codigo']}` | **{item['classe_abc']}** "
            f"| {item['estoque_disponivel']} ({item['estoque_atual']}+{item['estoque_em_transito']}) "
            f"| `{item['demanda_media_diaria_racional']}` | {item['estoque_seguranca']} "
            f"| {item['ponto_pedido']} | {cob_str} | `{item['status_reposicao']}` "
            f"| {item['deficit_unidades']} | {item['lote_minimo']} "
            f"| **{item['pedido_sugerido_unidades']} {item['unidade']}** ({item['lotes_sugeridos']}x) "
            f"| {_format_brl(item['valor_pedido_sugerido_centavos'])} |"
        )

    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _svg(report: dict[str, object]) -> bytes:
    """Renderiza painel visual SVG 1440x900 usando exclusivamente aritmética inteira."""
    inv = report["inventario"]  # type: ignore[index]
    param = report["parametros"]  # type: ignore[index]
    res_abc = report["resumo_abc"]  # type: ignore[index]
    geral = report["resumo_geral"]  # type: ignore[index]
    itens = report["itens"]  # type: ignore[index]
    status_cnt = geral["contagem_status_reposicao"]

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" viewBox="0 0 1440 900" role="img" aria-labelledby="title desc">',
        f'<title id="title">Curva ABC de Pareto e Diagnóstico de Reposição — {_xml_text(inv["titulo"])}</title>',
        '<desc id="desc">Gráfico estático de Pareto com barras de participação individual, curva acumulada exata, fronteiras A/B/C e painel de cobertura e ponto de pedido.</desc>',
        '<rect width="1440" height="900" fill="#f8fafc"/>',
        '<g font-family="DejaVu Sans, Segoe UI, Arial, sans-serif" fill="#0f172a">',
        # Header banner
        '<rect x="0" y="0" width="1440" height="96" fill="#0f172a"/>',
        '<text x="40" y="42" font-size="24" font-weight="bold" fill="#f8fafc">Curva ABC de Estoque · Classificação de Pareto e Ponto de Pedido</text>',
        f'<text x="40" y="72" font-size="13" fill="#cbd5e1">Inventário: {_xml_text(inv["titulo"])} ({_xml_text(inv["id"])}) · Ref.: {_xml_text(inv["data_referencia"])} · Janela: {param["dias_analise"]}d · Cortes A={_xml_text(param["limite_a_racional"])} / AB={_xml_text(param["limite_ab_racional"])}</text>',
        '<rect x="1140" y="24" width="260" height="48" rx="8" fill="#1e293b" stroke="#334155"/>',
        '<text x="1270" y="45" text-anchor="middle" font-size="12" fill="#94a3b8">FRAÇÕES EXATAS · SOMA 1/1</text>',
        '<text x="1270" y="63" text-anchor="middle" font-size="13" font-weight="bold" fill="#38bdf8">LOFRANO TECNOLOGIA · v1.0.0</text>',
    ]

    # Top KPI cards (y=114..198)
    kpis = [
        (
            "CONSUMO TOTAL NO PERÍODO",
            _format_brl(geral["valor_consumo_total_centavos"]),
            f"{geral['quantidade_itens']} itens · Soma racional {geral['soma_participacoes_racional']}",
            "#0f766e",
        ),
        (
            "CLASSE A (ALTO IMPACTO)",
            f"{res_abc['A']['quantidade_itens']} itens ({_format_bps(res_abc['A']['participacao_valor_bps'])})",
            f"{_format_brl(res_abc['A']['valor_consumo_centavos'])} · Fração {res_abc['A']['participacao_valor_racional']}",
            "#0284c7",
        ),
        (
            "CLASSE B (IMPACTO INTERMEDIÁRIO)",
            f"{res_abc['B']['quantidade_itens']} itens ({_format_bps(res_abc['B']['participacao_valor_bps'])})",
            f"{_format_brl(res_abc['B']['valor_consumo_centavos'])} · Fração {res_abc['B']['participacao_valor_racional']}",
            "#d97706",
        ),
        (
            "CLASSE C (CAUDA PARETO)",
            f"{res_abc['C']['quantidade_itens']} itens ({_format_bps(res_abc['C']['participacao_valor_bps'])})",
            f"{_format_brl(res_abc['C']['valor_consumo_centavos'])} · Fração {res_abc['C']['participacao_valor_racional']}",
            "#64748b",
        ),
        (
            "REPOSIÇÃO SUGERIDA",
            _format_brl(geral["valor_pedido_sugerido_total_centavos"]),
            f"Crítico: {status_cnt['critico']} · Repor: {status_cnt['repor']} · Emp. front.: {geral['itens_empate_fronteira']}",
            "#dc2626" if status_cnt["critico"] > 0 else "#15803d",
        ),
    ]

    for idx, (label, main_val, sub_val, accent) in enumerate(kpis):
        cx = 40 + idx * 274
        lines.extend(
            [
                f'<rect x="{cx}" y="114" width="258" height="84" rx="8" fill="#ffffff" stroke="#cbd5e1" stroke-width="1"/>',
                f'<rect x="{cx}" y="114" width="6" height="84" rx="3" fill="{accent}"/>',
                f'<text x="{cx + 18}" y="137" font-size="11" font-weight="bold" fill="#64748b">{label}</text>',
                f'<text x="{cx + 18}" y="163" font-size="17" font-weight="bold" fill="#0f172a">{_xml_text(main_val)}</text>',
                f'<text x="{cx + 18}" y="184" font-size="11" fill="#475569">{_xml_text(sub_val)}</text>',
            ]
        )

    # Left Panel: Pareto Chart (x=40, y=214, w=880, h=590)
    lines.extend(
        [
            '<rect x="40" y="214" width="880" height="590" rx="10" fill="#ffffff" stroke="#cbd5e1"/>',
            '<text x="64" y="246" font-size="16" font-weight="bold" fill="#0f172a">Diagrama de Pareto ABC (Valor Individual e Participação Acumulada)</text>',
            '<text x="64" y="266" font-size="12" fill="#475569">Barras representam a participação individual; a polilinha traça o percentual acumulado exato com cortes A e AB.</text>',
        ]
    )

    chart_x0 = 100
    chart_y0 = 300
    chart_w = 760
    chart_h = 410
    chart_bottom = chart_y0 + chart_h

    # Grid lines 0%, 25%, 50%, 75%, 100%
    for pct in (0, 25, 50, 75, 100):
        gy = chart_bottom - (pct * chart_h) // 100
        lines.append(
            f'<line x1="{chart_x0}" y1="{gy}" x2="{chart_x0 + chart_w}" y2="{gy}" stroke="#e2e8f0" stroke-width="1"/>'
        )
        lines.append(
            f'<text x="{chart_x0 - 10}" y="{gy + 4}" text-anchor="end" font-size="11" fill="#64748b">{pct}%</text>'
        )

    # Threshold lines for limite_a and limite_ab
    lim_a_y = chart_bottom - (int(param["limite_a_bps"]) * chart_h) // 10_000
    lim_ab_y = chart_bottom - (int(param["limite_ab_bps"]) * chart_h) // 10_000
    lines.extend(
        [
            f'<line x1="{chart_x0}" y1="{lim_a_y}" x2="{chart_x0 + chart_w}" y2="{lim_a_y}" stroke="#0284c7" stroke-width="1" stroke-dasharray="6,4"/>',
            f'<text x="{chart_x0 + chart_w - 6}" y="{lim_a_y - 5}" text-anchor="end" font-size="10" font-weight="bold" fill="#0284c7">Fronteira A ({_xml_text(param["limite_a_racional"])})</text>',
            f'<line x1="{chart_x0}" y1="{lim_ab_y}" x2="{chart_x0 + chart_w}" y2="{lim_ab_y}" stroke="#d97706" stroke-width="1" stroke-dasharray="6,4"/>',
            f'<text x="{chart_x0 + chart_w - 6}" y="{lim_ab_y - 5}" text-anchor="end" font-size="10" font-weight="bold" fill="#d97706">Fronteira AB ({_xml_text(param["limite_ab_racional"])})</text>',
        ]
    )

    shown_items = itens[:15]
    n_bars = max(1, len(shown_items))
    slot_w = chart_w // n_bars
    bar_w = max(14, slot_w - 14)

    class_colors = {"A": "#0284c7", "B": "#d97706", "C": "#64748b"}
    curve_points: list[str] = []

    for idx, item in enumerate(shown_items):
        slot_x = chart_x0 + idx * slot_w
        bx = slot_x + (slot_w - bar_w) // 2
        part_bps = int(item["participacao_bps"])
        acum_bps = int(item["participacao_acumulada_bps"])
        bar_h = (part_bps * chart_h) // 10_000
        if int(item["valor_consumo_centavos"]) > 0 and bar_h < 4:
            bar_h = 4
        by = chart_bottom - bar_h
        cy = chart_bottom - (acum_bps * chart_h) // 10_000
        cx_mid = bx + bar_w // 2
        curve_points.append(f"{cx_mid},{cy}")

        cl = str(item["classe_abc"])
        bar_fill = class_colors[cl]
        stroke_col = "#7c3aed" if item["empate_fronteira"] else bar_fill
        stroke_w = "2" if item["empate_fronteira"] else "0"

        codigo_xml = _xml_text(item["codigo"])
        lines.append(
            f'<g class="item-abc" data-codigo="{codigo_xml}" data-classe="{cl}" '
            f'data-posicao="{item["posicao_pareto"]}" '
            f'data-valor-consumo="{item["valor_consumo_centavos"]}" '
            f'data-participacao="{item["participacao_racional"]}" '
            f'data-acumulada="{item["participacao_acumulada_racional"]}" '
            f'data-status="{item["status_reposicao"]}" '
            f'data-empate-fronteira="{"true" if item["empate_fronteira"] else "false"}">'
        )
        lines.append(f'<title>{codigo_xml} — {_xml_text(item["descricao"])}</title>')
        if bar_h > 0:
            lines.append(
                f'<rect x="{bx}" y="{by}" width="{bar_w}" height="{bar_h}" rx="3" fill="{bar_fill}" stroke="{stroke_col}" stroke-width="{stroke_w}"/>'
            )
        if item["empate_fronteira"]:
            lines.append(
                f'<rect x="{cx_mid - 14}" y="{by - 18}" width="28" height="14" rx="3" fill="#7c3aed"/>'
            )
            lines.append(
                f'<text x="{cx_mid}" y="{by - 8}" text-anchor="middle" font-size="8" font-weight="bold" fill="#ffffff">EMP</text>'
            )
        lines.append(
            f'<text x="{cx_mid}" y="{chart_bottom + 18}" text-anchor="middle" font-size="10" font-weight="bold" fill="#1e293b">{codigo_xml}</text>'
        )
        lines.append(
            f'<text x="{cx_mid}" y="{chart_bottom + 32}" text-anchor="middle" font-size="9" fill="#64748b">{cl} · {_format_bps(part_bps)}</text>'
        )
        lines.append("</g>")

    # Include hidden metadata nodes for any items beyond the first 15 visual slots
    for item in itens[15:]:
        codigo_xml = _xml_text(item["codigo"])
        cl = str(item["classe_abc"])
        lines.append(
            f'<g class="item-abc" data-codigo="{codigo_xml}" data-classe="{cl}" '
            f'data-posicao="{item["posicao_pareto"]}" '
            f'data-valor-consumo="{item["valor_consumo_centavos"]}" '
            f'data-participacao="{item["participacao_racional"]}" '
            f'data-acumulada="{item["participacao_acumulada_racional"]}" '
            f'data-status="{item["status_reposicao"]}" '
            f'data-empate-fronteira="{"true" if item["empate_fronteira"] else "false"}">'
            f'<title>{codigo_xml}</title></g>'
        )

    if curve_points:
        lines.append(
            f'<polyline fill="none" stroke="#0f172a" stroke-width="3" points="{" ".join(curve_points)}"/>'
        )
        for pt in curve_points:
            px, py = pt.split(",")
            lines.append(
                f'<circle cx="{px}" cy="{py}" r="4" fill="#ffffff" stroke="#0f172a" stroke-width="2"/>'
            )

    # Right Panel: Replenishment Priority & Stock Coverage Table (x=940, y=214, w=460, h=590)
    lines.extend(
        [
            '<rect x="940" y="214" width="460" height="590" rx="10" fill="#ffffff" stroke="#cbd5e1"/>',
            '<text x="964" y="246" font-size="16" font-weight="bold" fill="#0f172a">Painel de Cobertura e Ponto de Pedido</text>',
            '<text x="964" y="266" font-size="12" fill="#475569">Saldo disponível vs. Ponto de Pedido e Lote Mínimo</text>',
        ]
    )

    status_badge = {
        "critico": ("#fef2f2", "#dc2626", "CRÍTICO"),
        "repor": ("#fffbeb", "#d97706", "REPOR"),
        "adequado": ("#f0fdf4", "#15803d", "ADEQUADO"),
        "sem_consumo": ("#f1f5f9", "#475569", "SEM CONSUMO"),
    }

    for idx, item in enumerate(shown_items):
        ry = 280 + idx * 34
        bg_col, txt_col, st_label = status_badge[str(item["status_reposicao"])]
        codigo_xml = _xml_text(item["codigo"])
        cob_txt = "—" if item["cobertura_dias_inteiro"] is None else f"{item['cobertura_dias_inteiro']}d"
        ped_txt = (
            f"Pedir {item['pedido_sugerido_unidades']} {item['unidade']}"
            if int(item["pedido_sugerido_unidades"]) > 0
            else "Sem pedido"
        )
        lines.extend(
            [
                f'<rect x="958" y="{ry}" width="424" height="28" rx="5" fill="#f8fafc" stroke="#e2e8f0"/>',
                f'<text x="968" y="{ry + 18}" font-size="11" font-weight="bold" fill="#0f172a">{codigo_xml} ({item["classe_abc"]})</text>',
                f'<text x="1062" y="{ry + 18}" font-size="10" fill="#334155">Disp:{item["estoque_disponivel"]} / PP:{item["ponto_pedido"]} · Cob:{cob_txt}</text>',
                f'<text x="1210" y="{ry + 18}" font-size="10" font-weight="bold" fill="#0f172a">{_xml_text(ped_txt)}</text>',
                f'<rect x="1308" y="{ry + 5}" width="68" height="18" rx="4" fill="{bg_col}" stroke="{txt_col}" stroke-width="1"/>',
                f'<text x="1342" y="{ry + 17}" text-anchor="middle" font-size="8" font-weight="bold" fill="{txt_col}">{st_label}</text>',
            ]
        )

    # Bottom Legend & Technical Note (y=818..880)
    legend_items = [
        ("#0284c7", "Classe A (Alta Prioridade)"),
        ("#d97706", "Classe B (Intermediária)"),
        ("#64748b", "Classe C (Cauda Pareto)"),
        ("#7c3aed", "Empate na Fronteira ABC"),
        ("#dc2626", "Status Crítico (Disp ≤ Seg)"),
        ("#15803d", "Status Adequado (Disp ≥ PP)"),
    ]
    lines.append('<rect x="40" y="818" width="1360" height="62" rx="8" fill="#ffffff" stroke="#cbd5e1"/>')
    for l_idx, (l_color, l_text) in enumerate(legend_items):
        lx = 58 + l_idx * 222
        lines.append(f'<rect x="{lx}" y="833" width="14" height="14" rx="3" fill="{l_color}"/>')
        lines.append(f'<text x="{lx + 20}" y="844" font-size="11" fill="#1e293b">{_xml_text(l_text)}</text>')
    lines.append(
        '<text x="58" y="868" font-size="11" fill="#64748b">Nota técnica: cálculo determinístico local em frações racionais exatas (soma 1/1); todos os valores constam em analise.json, curva-abc.csv e reposicao.csv.</text>'
    )
    lines.extend(["</g>", "</svg>", ""])
    return "\n".join(lines).encode("utf-8")


_LEIAME = """# Pacote de Análise — Curva ABC de Estoque

Este arquivo ZIP contém a classificação ABC de Pareto, a análise de cobertura
em dias e o dimensionamento de ponto de pedido com teto de lote mínimo
calculados em frações racionais exatas pelo motor `curva-abc-estoque`
(LOFRANO TECNOLOGIA, licença MIT).

## Arquivos incluídos

1. `entrada.json`: bytes exatos do documento de entrada submetido à análise.
2. `analise.json`: estrutura analítica completa com memória de cálculo item a
   item, participações racionais reduzidas (`p/q`) cuja soma fecha exatamente
   em `1/1`, detecção de empates de fronteira, cobertura e ponto de pedido.
3. `curva-abc.csv`: tabela ordenada de Pareto em UTF-8 (terminador LF) com
   proteção contra injeção de fórmulas de planilha.
4. `reposicao.csv`: tabela operacional de cobertura, estoque de segurança,
   ponto de pedido, status de reposição e pedido sugerido em múltiplos do lote
   mínimo.
5. `RELATORIO.md`: relatório textual auditável em português com tabelas de
   conferência e aviso de escopo técnico.
6. `curva-pareto.svg`: gráfico estático de Pareto e painel de reposição
   (1440×900) sem recursos externos.
7. `LEIA-ME.md`: este guia de leitura do pacote.
8. `SHA256SUMS`: hashes SHA-256 de todos os demais arquivos do pacote.

## Escopo e limites

O motor executa aritmética inteira e racional exata (`fractions.Fraction`)
sobre os dados declarados na entrada. Não substitui contagem física de
almoxarifado, auditoria contábil ou previsão estatística de demanda.
"""


def _same_json(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_same_json(v, right[k]) for k, v in left.items())
    if type(left) is list:
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def _verified_source(result: InventoryResult, input_bytes: bytes) -> None:
    if type(input_bytes) is not bytes:
        raise EstoqueError("REPORT_SOURCE", "A fonte do relatório deve ser fornecida em bytes originais.")
    if not _same_json(loads(input_bytes), result.source_document()):
        raise EstoqueError("REPORT_SOURCE", "Os bytes da entrada divergem do documento analisado.")


def build_report(result: InventoryResult, input_bytes: bytes) -> bytes:
    """Constrói e verifica em memória o pacote ZIP determinístico da análise ABC."""
    _verified_source(result, input_bytes)
    report_data = result.to_dict()
    files: dict[str, bytes] = {
        "LEIA-ME.md": _LEIAME.encode("utf-8"),
        "RELATORIO.md": _relatorio_md(report_data),
        "analise.json": dumps_output(report_data).encode("utf-8"),
        "curva-abc.csv": _curva_abc_csv(report_data),
        "curva-pareto.svg": _svg(report_data),
        "entrada.json": input_bytes,
        "reposicao.csv": _reposicao_csv(report_data),
    }
    sha_lines = [
        f"{hashlib.sha256(content).hexdigest()}  {name}\n"
        for name, content in sorted(files.items())
    ]
    files["SHA256SUMS"] = "".join(sha_lines).encode("ascii")

    if sum(map(len, files.values())) > MAX_BUNDLE_BYTES:
        raise EstoqueError("REPORT_LIMIT", "Pacote de análise excede o limite de 256 MiB descompactados.")

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    bundle_bytes = stream.getvalue()
    _verify_report(bundle_bytes, files)
    return bundle_bytes


def _verify_report(bundle_bytes: bytes, expected: dict[str, bytes]) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as archive:
            if tuple(archive.namelist()) != REPORT_FILES or archive.testzip() is not None:
                raise ValueError
            for name, content in expected.items():
                received = archive.read(name)
                if len(received) != len(content) or hashlib.sha256(received).digest() != hashlib.sha256(content).digest():
                    raise ValueError
    except (ValueError, OSError, zipfile.BadZipFile):
        raise EstoqueError("REPORT_INVALID", "Falha na verificação de integridade do pacote ZIP.") from None


def write_report(path: str | os.PathLike[str], result: InventoryResult, input_bytes: bytes) -> Path:
    """Publica o pacote ZIP exclusivamente por hardlink de arquivo temporário irmão."""
    content = build_report(result, input_bytes)
    target = Path(path)
    staged: Path | None = None
    try:
        if not target.parent.is_dir():
            raise EstoqueError("OUTPUT_PARENT", "O diretório de saída deve existir.")
        if os.path.lexists(target):
            raise EstoqueError("OUTPUT_EXISTS", "O destino já existe; não será sobrescrito.")
        descriptor, staged_name = tempfile.mkstemp(prefix=".curva-abc-estoque-", suffix=".tmp", dir=target.parent)
        staged = Path(staged_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), 0o644)
            os.fsync(handle.fileno())
        if staged.read_bytes() != content:
            raise EstoqueError("REPORT_INVALID", "Falha na conferência do arquivo preparado.")
        try:
            os.link(staged, target)
        except FileExistsError:
            raise EstoqueError("OUTPUT_EXISTS", "O destino já existe; não será sobrescrito.") from None
        except OSError:
            raise EstoqueError(
                "OUTPUT_LINK",
                "Não foi possível publicar por hardlink exclusivo neste sistema de arquivos.",
            ) from None
        return target
    except EstoqueError:
        raise
    except (OSError, ValueError):
        raise EstoqueError("OUTPUT_IO", "Não foi possível preparar o pacote no diretório de saída.") from None
    finally:
        if staged is not None:
            try:
                staged.unlink()
            except OSError:
                pass
