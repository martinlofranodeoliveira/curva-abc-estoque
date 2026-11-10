#!/usr/bin/env python3
"""Verificador independente de pacotes ZIP gerados por curva-abc-estoque.

Usa apenas a biblioteca padrão do Python (sem importar curva_abc_estoque):
1. Oráculo racional exato com fractions.Fraction (Pareto, empates, cobertura e lote mínimo).
2. Oráculo relacional SQLite (window functions e agregações SQL sobre curva-abc.csv e reposicao.csv).
3. Verificação de SHA256SUMS, CSV (neutralização de fórmulas), SVG (XML seguro) e ouro manual (--demo).
"""
from __future__ import annotations

import argparse
import csv
from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]

NAMES = (
    "LEIA-ME.md",
    "RELATORIO.md",
    "SHA256SUMS",
    "analise.json",
    "curva-abc.csv",
    "curva-pareto.svg",
    "entrada.json",
    "reposicao.csv",
)

DEMO_SUMMARY = {
    "quantidade_itens": 15,
    "soma_participacoes_racional": "1/1",
    "valor_consumo_total_centavos": 2000000,
    "valor_estoque_atual_total_centavos": 521600,
    "valor_estoque_disponivel_total_centavos": 546600,
    "valor_pedido_sugerido_total_centavos": 334000,
    "unidades_pedido_sugerido_total": 27,
    "itens_empate_valor": 2,
    "itens_empate_fronteira": 2,
    "contagem_status_reposicao": {
        "critico": 4,
        "repor": 4,
        "adequado": 6,
        "sem_consumo": 1,
    },
}

DEMO_CLASSES_GOLD = {
    "A": {
        "quantidade_itens": 4,
        "participacao_itens_racional": "4/15",
        "valor_consumo_centavos": 1600000,
        "participacao_valor_racional": "4/5",
        "valor_estoque_atual_centavos": 365000,
        "valor_pedido_sugerido_centavos": 240000,
    },
    "B": {
        "quantidade_itens": 4,
        "participacao_itens_racional": "4/15",
        "valor_consumo_centavos": 300000,
        "participacao_valor_racional": "3/20",
        "valor_estoque_atual_centavos": 95000,
        "valor_pedido_sugerido_centavos": 55000,
    },
    "C": {
        "quantidade_itens": 7,
        "participacao_itens_racional": "7/15",
        "valor_consumo_centavos": 100000,
        "participacao_valor_racional": "1/20",
        "valor_estoque_atual_centavos": 61600,
        "valor_pedido_sugerido_centavos": 39000,
    },
}


class CheckError(ValueError):
    """Falha de verificação independente."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckError(message)


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        require(key not in result, "JSON contém chave duplicada")
        result[key] = value
    return result


def _reject_float(_token: str) -> None:
    raise CheckError("JSON contém ponto flutuante não permitido")


def _reject_constant(_token: str) -> None:
    raise CheckError("JSON contém constante não finita")


def load_json(data: bytes | str) -> object:
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="strict")
    else:
        text = data
    require(not text.startswith("\ufeff"), "JSON contém BOM")
    return json.loads(
        text,
        parse_int=int,
        parse_float=_reject_float,
        parse_constant=_reject_constant,
        object_pairs_hook=_pairs,
    )


def json_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return left == right


def csv_safe(value: str) -> str:
    stripped = value.lstrip(" ")
    if stripped.startswith(("=", "+", "-", "@", "\t", "\r")) or value.startswith(("\t", "\r")):
        return "'" + value
    return value


def parse_frac(text: str) -> Fraction:
    num_str, den_str = text.split("/")
    return Fraction(int(num_str), int(den_str))


def ceil_frac(value: Fraction) -> int:
    if value <= 0:
        return 0
    return (value.numerator + value.denominator - 1) // value.denominator


def fraction_oracle(entrada_doc: dict[str, object], analise_doc: dict[str, object]) -> int:
    """Recalcula independentemente toda a análise ABC e reposição via Fraction e confronta analise.json."""
    param = entrada_doc["parametros"]  # type: ignore[index]
    dias_analise = int(param["dias_analise"])
    limite_a = parse_frac(param["limite_a_racional"])
    limite_ab = parse_frac(param["limite_ab_racional"])
    dias_seg_padrao = int(param["dias_seguranca_padrao"])

    itens_in = list(entrada_doc["itens"])  # type: ignore[arg-type]
    itens_out = list(analise_doc["itens"])  # type: ignore[arg-type]
    require(len(itens_in) == len(itens_out), "Quantidade de itens diverge entre entrada e análise")

    enriched: list[dict[str, object]] = []
    total_val = 0
    for it in itens_in:
        val = int(it["consumo_periodo"]) * int(it["custo_unitario_centavos"])
        total_val += val
        copy_it = dict(it)
        copy_it["valor_consumo_centavos"] = val
        enriched.append(copy_it)

    require(total_val > 0, "Total de consumo deve ser positivo")
    enriched.sort(
        key=lambda x: (
            -int(x["valor_consumo_centavos"]),  # type: ignore[arg-type]
            -int(x["custo_unitario_centavos"]),  # type: ignore[arg-type]
            str(x["codigo"]),
        )
    )

    freq_val: dict[int, int] = {}
    for x in enriched:
        v = int(x["valor_consumo_centavos"])  # type: ignore[arg-type]
        freq_val[v] = freq_val.get(v, 0) + 1

    acum = 0
    soma_shares = Fraction(0, 1)
    classes_por_val: dict[int, set[str]] = {}
    expected_classes: list[str] = []

    for pos, (x_in, x_out) in enumerate(zip(enriched, itens_out), start=1):
        require(x_out["posicao_pareto"] == pos, "Posição de Pareto diverge")
        require(x_out["codigo"] == x_in["codigo"], "Ordenação de código em Pareto diverge")
        val = int(x_in["valor_consumo_centavos"])  # type: ignore[arg-type]
        custo = int(x_in["custo_unitario_centavos"])  # type: ignore[arg-type]
        consumo = int(x_in["consumo_periodo"])  # type: ignore[arg-type]
        est_atual = int(x_in["estoque_atual"])  # type: ignore[arg-type]
        est_transito = int(x_in.get("estoque_em_transito", 0))  # type: ignore[arg-type]
        est_disp = est_atual + est_transito
        lead_time = int(x_in["lead_time_dias"])  # type: ignore[arg-type]
        dias_seg = int(x_in.get("dias_seguranca", dias_seg_padrao))  # type: ignore[arg-type]
        lote_min = int(x_in["lote_minimo"])  # type: ignore[arg-type]

        part_ant = Fraction(acum, total_val)
        acum += val
        part_ind = Fraction(val, total_val)
        part_acum = Fraction(acum, total_val)
        soma_shares += part_ind

        if val == 0:
            cl = "C"
        elif part_acum <= limite_a or pos == 1:
            cl = "A"
        elif part_acum <= limite_ab:
            cl = "B"
        else:
            cl = "C"

        classes_por_val.setdefault(val, set()).add(cl)
        expected_classes.append(cl)

        require(x_out["valor_consumo_centavos"] == val, "valor_consumo_centavos diverge")
        require(x_out["valor_consumo_acumulado_centavos"] == acum, "valor_consumo_acumulado_centavos diverge")
        require(x_out["participacao_racional"] == f"{part_ind.numerator}/{part_ind.denominator}", "participacao_racional diverge")
        require(
            x_out["participacao_anterior_racional"] == f"{part_ant.numerator}/{part_ant.denominator}",
            "participacao_anterior_racional diverge",
        )
        require(
            x_out["participacao_acumulada_racional"] == f"{part_acum.numerator}/{part_acum.denominator}",
            "participacao_acumulada_racional diverge",
        )
        require(x_out["classe_abc"] == cl, "classe_abc diverge do oráculo")
        require(x_out["empate_valor"] is (freq_val[val] > 1), "empate_valor diverge")

        dem_lt = ceil_frac(Fraction(consumo * lead_time, dias_analise))
        est_seg = ceil_frac(Fraction(consumo * dias_seg, dias_analise))
        pp = dem_lt + est_seg

        require(x_out["estoque_disponivel"] == est_disp, "estoque_disponivel diverge")
        require(x_out["demanda_lead_time"] == dem_lt, "demanda_lead_time diverge")
        require(x_out["estoque_seguranca"] == est_seg, "estoque_seguranca diverge")
        require(x_out["ponto_pedido"] == pp, "ponto_pedido diverge")

        if consumo > 0:
            cob_f = Fraction(est_disp * dias_analise, consumo)
            require(
                x_out["cobertura_dias_racional"] == f"{cob_f.numerator}/{cob_f.denominator}",
                "cobertura_dias_racional diverge",
            )
            require(x_out["cobertura_dias_inteiro"] == (est_disp * dias_analise) // consumo, "cobertura_dias_inteiro diverge")
        else:
            require(x_out["cobertura_dias_racional"] is None, "cobertura_dias_racional deve ser null sem consumo")
            require(x_out["cobertura_dias_inteiro"] is None, "cobertura_dias_inteiro deve ser null sem consumo")

        if consumo == 0:
            st = "sem_consumo"
            deficit = 0
            lotes = 0
            pedido = 0
        elif est_disp <= est_seg:
            st = "critico"
            deficit = pp - est_disp
            lotes = (deficit + lote_min - 1) // lote_min
            pedido = lotes * lote_min
        elif est_disp < pp:
            st = "repor"
            deficit = pp - est_disp
            lotes = (deficit + lote_min - 1) // lote_min
            pedido = lotes * lote_min
        else:
            st = "adequado"
            deficit = 0
            lotes = 0
            pedido = 0

        require(x_out["status_reposicao"] == st, "status_reposicao diverge")
        require(x_out["deficit_unidades"] == deficit, "deficit_unidades diverge")
        require(x_out["lotes_sugeridos"] == lotes, "lotes_sugeridos diverge")
        require(x_out["pedido_sugerido_unidades"] == pedido, "pedido_sugerido_unidades diverge")
        require(x_out["valor_pedido_sugerido_centavos"] == pedido * custo, "valor_pedido_sugerido_centavos diverge")

    require(soma_shares == Fraction(1, 1), "Soma das participações racionais não é 1/1")
    require(analise_doc["resumo_geral"]["soma_participacoes_racional"] == "1/1", "resumo_geral soma_participacoes != 1/1")  # type: ignore[index]

    for x_out in itens_out:
        v = int(x_out["valor_consumo_centavos"])
        require(x_out["empate_fronteira"] is (len(classes_por_val[v]) > 1), "empate_fronteira diverge do oráculo")

    return len(itens_out)


def sqlite_oracle(abc_bytes: bytes, rep_bytes: bytes, analise_doc: dict[str, object]) -> int:
    """Importa curva-abc.csv e reposicao.csv no SQLite em memória e confere agregações e window functions SQL."""
    abc_text = abc_bytes.decode("utf-8")
    rep_text = rep_bytes.decode("utf-8")
    require("\r" not in abc_text and abc_text.endswith("\n"), "curva-abc.csv deve usar LF")
    require("\r" not in rep_text and rep_text.endswith("\n"), "reposicao.csv deve usar LF")

    abc_rows = list(csv.DictReader(io.StringIO(abc_text, newline="")))
    rep_rows = list(csv.DictReader(io.StringIO(rep_text, newline="")))
    require(len(abc_rows) == len(rep_rows) == len(analise_doc["itens"]), "Contagem de linhas CSV diverge")  # type: ignore[arg-type]

    conn = sqlite3.connect(":memory:")
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE curva_abc (
                posicao_pareto INTEGER PRIMARY KEY,
                codigo TEXT NOT NULL,
                descricao TEXT NOT NULL,
                categoria TEXT NOT NULL,
                unidade TEXT NOT NULL,
                custo_unitario_centavos INTEGER NOT NULL,
                consumo_periodo INTEGER NOT NULL,
                valor_consumo_centavos INTEGER NOT NULL,
                valor_consumo_acumulado_centavos INTEGER NOT NULL,
                participacao_racional TEXT NOT NULL,
                participacao_acumulada_racional TEXT NOT NULL,
                classe_abc TEXT NOT NULL,
                empate_valor INTEGER NOT NULL,
                empate_fronteira INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE reposicao (
                posicao_pareto INTEGER PRIMARY KEY,
                codigo TEXT NOT NULL,
                classe_abc TEXT NOT NULL,
                estoque_atual INTEGER NOT NULL,
                estoque_em_transito INTEGER NOT NULL,
                estoque_disponivel INTEGER NOT NULL,
                lead_time_dias INTEGER NOT NULL,
                dias_seguranca INTEGER NOT NULL,
                demanda_lead_time INTEGER NOT NULL,
                estoque_seguranca INTEGER NOT NULL,
                ponto_pedido INTEGER NOT NULL,
                cobertura_dias_inteiro INTEGER,
                status_reposicao TEXT NOT NULL,
                deficit_unidades INTEGER NOT NULL,
                lote_minimo INTEGER NOT NULL,
                lotes_sugeridos INTEGER NOT NULL,
                pedido_sugerido_unidades INTEGER NOT NULL,
                valor_pedido_sugerido_centavos INTEGER NOT NULL
            )
            """
        )

        for r_abc, r_rep, it_json in zip(abc_rows, rep_rows, analise_doc["itens"]):  # type: ignore[index]
            require(r_abc["codigo"] == csv_safe(it_json["codigo"]), "Neutralização CSV de codigo incorreta")
            require(r_abc["descricao"] == csv_safe(it_json["descricao"]), "Neutralização CSV de descricao incorreta")
            require(r_abc["categoria"] == csv_safe(it_json["categoria"]), "Neutralização CSV de categoria incorreta")
            require(r_rep["descricao"] == csv_safe(it_json["descricao"]), "Neutralização CSV em reposicao.csv incorreta")

            cur.execute(
                """
                INSERT INTO curva_abc VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(r_abc["posicao_pareto"]),
                    r_abc["codigo"],
                    r_abc["descricao"],
                    r_abc["categoria"],
                    r_abc["unidade"],
                    int(r_abc["custo_unitario_centavos"]),
                    int(r_abc["consumo_periodo"]),
                    int(r_abc["valor_consumo_centavos"]),
                    int(r_abc["valor_consumo_acumulado_centavos"]),
                    r_abc["participacao_racional"],
                    r_abc["participacao_acumulada_racional"],
                    r_abc["classe_abc"],
                    1 if r_abc["empate_valor"] == "true" else 0,
                    1 if r_abc["empate_fronteira"] == "true" else 0,
                ),
            )
            cur.execute(
                """
                INSERT INTO reposicao VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(r_rep["posicao_pareto"]),
                    r_rep["codigo"],
                    r_rep["classe_abc"],
                    int(r_rep["estoque_atual"]),
                    int(r_rep["estoque_em_transito"]),
                    int(r_rep["estoque_disponivel"]),
                    int(r_rep["lead_time_dias"]),
                    int(r_rep["dias_seguranca"]),
                    int(r_rep["demanda_lead_time"]),
                    int(r_rep["estoque_seguranca"]),
                    int(r_rep["ponto_pedido"]),
                    None if r_rep["cobertura_dias_inteiro"] == "" else int(r_rep["cobertura_dias_inteiro"]),
                    r_rep["status_reposicao"],
                    int(r_rep["deficit_unidades"]),
                    int(r_rep["lote_minimo"]),
                    int(r_rep["lotes_sugeridos"]),
                    int(r_rep["pedido_sugerido_unidades"]),
                    int(r_rep["valor_pedido_sugerido_centavos"]),
                ),
            )

        integrity = cur.execute("PRAGMA integrity_check").fetchone()[0]
        require(integrity == "ok", "Falha em PRAGMA integrity_check no SQLite")

        # Window function SQL para conferir soma acumulada de Pareto
        win_rows = cur.execute(
            """
            SELECT
                posicao_pareto,
                SUM(valor_consumo_centavos) OVER (
                    ORDER BY posicao_pareto
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ),
                valor_consumo_acumulado_centavos
            FROM curva_abc
            ORDER BY posicao_pareto
            """
        ).fetchall()
        for _, sql_acum, csv_acum in win_rows:
            require(sql_acum == csv_acum, "Window function SQL de valor acumulado diverge")

        # Conferência por classe ABC via JOIN entre curva_abc e reposicao
        for cl in ("A", "B", "C"):
            row = cur.execute(
                """
                SELECT
                    COUNT(*),
                    COALESCE(SUM(a.valor_consumo_centavos), 0),
                    COALESCE(SUM(r.estoque_atual * a.custo_unitario_centavos), 0),
                    COALESCE(SUM(r.valor_pedido_sugerido_centavos), 0)
                FROM curva_abc a
                JOIN reposicao r ON a.posicao_pareto = r.posicao_pareto
                WHERE a.classe_abc = ?
                """,
                (cl,),
            ).fetchone()
            expected_cl = analise_doc["resumo_abc"][cl]  # type: ignore[index]
            require(
                row
                == (
                    expected_cl["quantidade_itens"],
                    expected_cl["valor_consumo_centavos"],
                    expected_cl["valor_estoque_atual_centavos"],
                    expected_cl["valor_pedido_sugerido_centavos"],
                ),
                f"Agregação SQL da classe {cl} diverge de analise.json",
            )

        # Conferência de status_reposicao via GROUP BY
        status_rows = dict(
            cur.execute("SELECT status_reposicao, COUNT(*) FROM reposicao GROUP BY status_reposicao").fetchall()
        )
        for st, exp_cnt in analise_doc["resumo_geral"]["contagem_status_reposicao"].items():  # type: ignore[index]
            require(status_rows.get(st, 0) == exp_cnt, f"Contagem SQL do status {st} diverge")

        return len(abc_rows)
    finally:
        conn.close()


def check_svg(svg_bytes: bytes, analise_doc: dict[str, object]) -> None:
    require(b"<!DOCTYPE" not in svg_bytes.upper() and b"<!ENTITY" not in svg_bytes.upper(), "SVG contém DTD/ENTITY")
    root = ET.fromstring(svg_bytes)
    require(root.tag == "{http://www.w3.org/2000/svg}svg", "Raiz SVG inesperada")
    actual_items: dict[str, tuple[str, int, int, str, str, str, bool]] = {}
    for node in root.iter():
        tag_name = node.tag.rsplit("}", 1)[-1]
        require(tag_name not in {"script", "foreignObject", "image", "use"}, "SVG contém elemento proibido")
        for attr_k, attr_v in node.attrib.items():
            require(
                not attr_k.lower().startswith("on")
                and "href" not in attr_k.lower()
                and "url(" not in attr_v.lower(),
                "SVG contém atributo externo ou evento",
            )
        if node.get("class") == "item-abc":
            cod = node.get("data-codigo", "")
            require(cod not in actual_items, "SVG repete código de item")
            actual_items[cod] = (
                node.get("data-classe", ""),
                int(node.get("data-posicao", "-1")),
                int(node.get("data-valor-consumo", "-1")),
                node.get("data-participacao", ""),
                node.get("data-acumulada", ""),
                node.get("data-status", ""),
                node.get("data-empate-fronteira") == "true",
            )
    expected_items = {
        it["codigo"]: (
            it["classe_abc"],
            it["posicao_pareto"],
            it["valor_consumo_centavos"],
            it["participacao_racional"],
            it["participacao_acumulada_racional"],
            it["status_reposicao"],
            bool(it["empate_fronteira"]),
        )
        for it in analise_doc["itens"]  # type: ignore[index]
    }
    require(actual_items == expected_items, "Atributos analíticos do SVG divergem de analise.json")


def check_demo(analise_doc: dict[str, object]) -> None:
    require(json_equal(analise_doc["resumo_geral"], DEMO_SUMMARY), "Resumo geral do exemplo diverge do ouro manual")
    for cl, gold in DEMO_CLASSES_GOLD.items():
        actual_cl = analise_doc["resumo_abc"][cl]  # type: ignore[index]
        for k, v in gold.items():
            require(actual_cl[k] == v, f"Campo {k} da classe {cl} diverge do ouro manual")


def check_bundle(path: Path, *, demo: bool = False, input_bytes: bytes | None = None) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        require(tuple(entry.filename for entry in infos) == NAMES, "Nomes ou ordem do ZIP divergem do contrato")
        for entry in infos:
            require(entry.date_time == (1980, 1, 1, 0, 0, 0), "Timestamp ZIP não determinístico")
            require(stat.S_IMODE(entry.external_attr >> 16) == 0o644, "Permissão ZIP inesperada")
            require(not (entry.flag_bits & 1), "ZIP criptografado não suportado")
        require(archive.testzip() is None, "CRC do ZIP inválido")
        files = {name: archive.read(name) for name in NAMES}

    sha_lines = files["SHA256SUMS"].decode("ascii").splitlines()
    expected_sha = [
        f"{hashlib.sha256(files[name]).hexdigest()}  {name}"
        for name in NAMES
        if name != "SHA256SUMS"
    ]
    require(sha_lines == expected_sha, "SHA256SUMS interno não confere com os arquivos do pacote")

    if input_bytes is not None:
        require(files["entrada.json"] == input_bytes, "Bytes de entrada.json divergem da fonte")

    entrada_doc = load_json(files["entrada.json"])
    analise_doc = load_json(files["analise.json"])
    require(isinstance(entrada_doc, dict) and isinstance(analise_doc, dict), "Raiz JSON deve ser objeto")

    items_checked = fraction_oracle(entrada_doc, analise_doc)
    sql_rows = sqlite_oracle(files["curva-abc.csv"], files["reposicao.csv"], analise_doc)
    check_svg(files["curva-pareto.svg"], analise_doc)

    relatorio_txt = files["RELATORIO.md"].decode("utf-8")
    leiame_txt = files["LEIA-ME.md"].decode("utf-8")
    require("Aviso de escopo técnico" in relatorio_txt and len(leiame_txt.strip()) > 100, "Documentação interna incompleta")

    if demo:
        check_demo(analise_doc)
        require(sql_rows == 15, "Exemplo exige 15 itens analisados")

    return {
        "ok": True,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "fractionItemsVerified": items_checked,
        "sqliteRowsVerified": sql_rows,
        "summary": analise_doc["resumo_geral"],
    }


def _check_network_isolation() -> bool:
    if sys.platform.startswith("linux"):
        try:
            ifaces = {name for _, name in socket.if_nameindex()}
            require(ifaces <= {"lo"}, "Interface de rede externa visível no namespace isolado")
        except OSError:
            net_dir = Path("/sys/class/net")
            if net_dir.is_dir():
                ifaces = {p.name for p in net_dir.iterdir()}
                require(ifaces <= {"lo"}, "Interface de rede externa visível no namespace isolado")
        route_file = Path("/proc/net/route")
        if route_file.is_file():
            lines = [ln.strip() for ln in route_file.read_text(encoding="ascii").splitlines() if ln.strip()]
            require(len(lines) <= 1, "Tabela de rotas IPv4 não está vazia no namespace isolado")
    return True


def _synthetic_edge_scenario() -> dict[str, object]:
    """Cenário de borda: empates de valor e fronteira B/C, teto de lote mínimo e líderes de fórmula CSV."""
    return {
        "schema_version": 1,
        "inventario": {
            "id": "qa-borda-abc-2026",
            "titulo": "Cenario Sintetico de Fronteiras Exatas ABC",
            "data_referencia": "2026-06-30",
            "moeda": "BRL",
        },
        "parametros": {
            "dias_analise": 60,
            "limite_a_racional": "7/10",
            "limite_ab_racional": "9/10",
            "dias_seguranca_padrao": 5,
        },
        "itens": [
            {
                "codigo": "QA-01",
                "descricao": "=Motor Trifásico Alta Eficiência",
                "categoria": "+Acionamentos",
                "unidade": "UN",
                "custo_unitario_centavos": 70000,
                "consumo_periodo": 10,
                "estoque_atual": 1,
                "estoque_em_transito": 0,
                "lead_time_dias": 12,
                "dias_seguranca": 6,
                "lote_minimo": 2,
            },
            {
                "codigo": "QA-02",
                "descricao": "-Bomba Centrífuga Inox",
                "categoria": "@Hidráulica",
                "unidade": "UN",
                "custo_unitario_centavos": 25000,
                "consumo_periodo": 4,
                "estoque_atual": 1,
                "estoque_em_transito": 0,
                "lead_time_dias": 15,
                "dias_seguranca": 5,
                "lote_minimo": 3,
            },
            {
                "codigo": "QA-03",
                "descricao": "Redutor Planetário Eixo Oco",
                "categoria": "Transmissão",
                "unidade": "UN",
                "custo_unitario_centavos": 20000,
                "consumo_periodo": 5,
                "estoque_atual": 2,
                "estoque_em_transito": 0,
                "lead_time_dias": 15,
                "dias_seguranca": 5,
                "lote_minimo": 2,
            },
            {
                "codigo": "QA-04",
                "descricao": "Manômetro Glicerinado Inox",
                "categoria": "Instrumentação",
                "unidade": "UN",
                "custo_unitario_centavos": 10000,
                "consumo_periodo": 10,
                "estoque_atual": 10,
                "estoque_em_transito": 0,
                "lead_time_dias": 12,
                "dias_seguranca": 6,
                "lote_minimo": 5,
            },
            {
                "codigo": "QA-05",
                "descricao": "Pressostato Reserva Sem Giro",
                "categoria": "Instrumentação",
                "unidade": "UN",
                "custo_unitario_centavos": 15000,
                "consumo_periodo": 0,
                "estoque_atual": 2,
                "estoque_em_transito": 0,
                "lead_time_dias": 10,
                "dias_seguranca": 5,
                "lote_minimo": 1,
            },
        ],
    }


def run_cli_suite(cli_cmd: list[str], *, require_net_isolation: bool = False, extra_env: dict[str, str] | None = None) -> dict[str, object]:
    if require_net_isolation:
        _check_network_isolation()
    with tempfile.TemporaryDirectory(prefix="curva-abc-qa-") as tmp:
        tmp_dir = Path(tmp)
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
        env.update(PYTHONUTF8="1", PYTHONNOUSERSITE="1", HOME=str(tmp_dir))
        if extra_env:
            env.update(extra_env)

        demo_zip = tmp_dir / "demo.zip"
        proc_demo = subprocess.run([*cli_cmd, "exemplo", "--saida", str(demo_zip)], cwd=tmp_dir, env=env, capture_output=True, text=True)
        require(proc_demo.returncode == 0, f"Falha ao executar CLI exemplo: {proc_demo.stderr}")
        demo_report = check_bundle(demo_zip, demo=True)

        edge_doc = _synthetic_edge_scenario()
        edge_json = tmp_dir / "borda.json"
        edge_bytes = (json.dumps(edge_doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        edge_json.write_bytes(edge_bytes)
        edge_zip = tmp_dir / "borda.zip"
        proc_edge = subprocess.run([*cli_cmd, "analisar", str(edge_json), "--saida", str(edge_zip)], cwd=tmp_dir, env=env, capture_output=True, text=True)
        require(proc_edge.returncode == 0, f"Falha ao executar CLI analisar no cenário de borda: {proc_edge.stderr}")
        edge_report = check_bundle(edge_zip, demo=False, input_bytes=edge_bytes)

        return {
            "ok": True,
            "networkIsolationChecked": require_net_isolation,
            "demo": demo_report,
            "edgeScenario": edge_report,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verificador independente de pacotes curva-abc-estoque")
    parser.add_argument("bundle", nargs="?", type=Path, help="Pacote ZIP para verificar diretamente")
    parser.add_argument("--demo", action="store_true", help="Confere o ouro manual do cenário exemplo")
    parser.add_argument("--entrada", type=Path, help="Compara bytes exatos de entrada.json")
    parser.add_argument("--cli", type=Path, help="Executa bateria independente contra o executável CLI instalado")
    parser.add_argument("--require-network-isolation", action="store_true", help="Exige namespace de rede isolado")
    parser.add_argument("--report", type=Path, help="Salva relatório JSON da verificação")
    args = parser.parse_args(argv)
    try:
        if args.cli:
            payload = run_cli_suite([str(args.cli)], require_net_isolation=args.require_network_isolation)
        elif args.bundle:
            if args.require_network_isolation:
                _check_network_isolation()
            payload = check_bundle(
                args.bundle,
                demo=args.demo,
                input_bytes=args.entrada.read_bytes() if args.entrada else None,
            )
        else:
            cli_which = shutil.which("curva-abc-estoque")
            if cli_which:
                payload = run_cli_suite([cli_which], require_net_isolation=args.require_network_isolation)
            else:
                payload = run_cli_suite(
                    [sys.executable, "-m", "curva_abc_estoque"],
                    require_net_isolation=args.require_network_isolation,
                    extra_env={"PYTHONPATH": str(ROOT / "src")},
                )
        output = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output + "\n", encoding="utf-8")
        print(output)
        return 0
    except Exception as exc:
        print(f"Falha na verificação independente: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
