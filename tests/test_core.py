from __future__ import annotations

from fractions import Fraction
from importlib import resources
import unittest

from curva_abc_estoque import EstoqueError, analisar, analisar_json


def _base_doc() -> dict[str, object]:
    return {
        "schema_version": 1,
        "inventario": {
            "id": "inv-teste-01",
            "titulo": "Inventário de Teste — Oficina",
            "data_referencia": "2026-03-31",
            "moeda": "BRL",
        },
        "parametros": {
            "dias_analise": 90,
            "limite_a_racional": "4/5",
            "limite_ab_racional": "19/20",
            "dias_seguranca_padrao": 6,
        },
        "itens": [
            {
                "codigo": "IT-A1",
                "descricao": "Conjunto Hidráulico Principal",
                "categoria": "Hidráulica",
                "unidade": "UN",
                "custo_unitario_centavos": 80000,
                "consumo_periodo": 10,
                "estoque_atual": 1,
                "estoque_em_transito": 0,
                "lead_time_dias": 18,
                "dias_seguranca": 9,
                "lote_minimo": 2,
            },
            {
                "codigo": "IT-B1",
                "descricao": "Kit de Vedação Viton",
                "categoria": "Vedação",
                "unidade": "KIT",
                "custo_unitario_centavos": 15000,
                "consumo_periodo": 10,
                "estoque_atual": 2,
                "estoque_em_transito": 0,
                "lead_time_dias": 18,
                "dias_seguranca": 9,
                "lote_minimo": 5,
            },
            {
                "codigo": "IT-C1",
                "descricao": "Parafuso Sextavado Inox M12",
                "categoria": "Fixação",
                "unidade": "CTO",
                "custo_unitario_centavos": 5000,
                "consumo_periodo": 10,
                "estoque_atual": 10,
                "estoque_em_transito": 0,
                "lead_time_dias": 9,
                "dias_seguranca": 9,
                "lote_minimo": 1,
            },
            {
                "codigo": "IT-Z0",
                "descricao": "Peça Reserva Sem Movimentação",
                "categoria": "Reserva",
                "unidade": "UN",
                "custo_unitario_centavos": 12000,
                "consumo_periodo": 0,
                "estoque_atual": 3,
                "estoque_em_transito": 0,
                "lead_time_dias": 15,
                "dias_seguranca": 6,
                "lote_minimo": 1,
            },
        ],
    }


class TestCoreCalculation(unittest.TestCase):
    def test_example_dataset_matches_exact_pareto_and_replenishment_gold(self) -> None:
        raw = resources.files("curva_abc_estoque").joinpath("data/exemplo.json").read_bytes()
        res = analisar_json(raw)
        report = res.to_dict()
        geral = report["resumo_geral"]
        self.assertEqual(geral["quantidade_itens"], 15)
        self.assertEqual(geral["soma_participacoes_racional"], "1/1")
        self.assertEqual(geral["valor_consumo_total_centavos"], 2_000_000)
        self.assertEqual(geral["itens_empate_valor"], 2)
        self.assertEqual(geral["itens_empate_fronteira"], 2)
        self.assertEqual(
            geral["contagem_status_reposicao"],
            {"critico": 4, "repor": 4, "adequado": 6, "sem_consumo": 1},
        )
        self.assertEqual(report["resumo_abc"]["A"]["participacao_valor_racional"], "4/5")
        self.assertEqual(report["resumo_abc"]["B"]["participacao_valor_racional"], "3/20")
        self.assertEqual(report["resumo_abc"]["C"]["participacao_valor_racional"], "1/20")

    def test_exact_fractions_sum_to_one_for_prime_denominators(self) -> None:
        doc = _base_doc()
        doc["itens"] = [
            {
                "codigo": f"P-{idx:02d}",
                "descricao": f"Item Primo {idx}",
                "categoria": "Teste",
                "unidade": "UN",
                "custo_unitario_centavos": cost,
                "consumo_periodo": qty,
                "estoque_atual": 5,
                "lead_time_dias": 10,
                "lote_minimo": 1,
            }
            for idx, (cost, qty) in enumerate([(101, 3), (103, 7), (107, 11), (109, 13), (113, 17)], start=1)
        ]
        res = analisar(doc)
        d = res.to_dict()
        self.assertEqual(d["resumo_geral"]["soma_participacoes_racional"], "1/1")
        total_frac = sum((res.share_fraction(it["codigo"]) for it in d["itens"]), Fraction(0, 1))
        self.assertEqual(total_frac, Fraction(1, 1))
        last_code = d["itens"][-1]["codigo"]
        self.assertEqual(res.cumulative_share_fraction(last_code), Fraction(1, 1))

    def test_boundary_tie_detection_across_a_and_b(self) -> None:
        doc = _base_doc()
        doc["itens"] = [
            {
                "codigo": "A-TOP",
                "descricao": "Item Líder",
                "categoria": "A",
                "unidade": "UN",
                "custo_unitario_centavos": 70000,
                "consumo_periodo": 1,
                "estoque_atual": 5,
                "lead_time_dias": 10,
                "lote_minimo": 1,
            },
            {
                "codigo": "TIE-1",
                "descricao": "Empate Fronteira 1",
                "categoria": "A",
                "unidade": "UN",
                "custo_unitario_centavos": 10000,
                "consumo_periodo": 1,
                "estoque_atual": 5,
                "lead_time_dias": 10,
                "lote_minimo": 1,
            },
            {
                "codigo": "TIE-2",
                "descricao": "Empate Fronteira 2",
                "categoria": "B",
                "unidade": "UN",
                "custo_unitario_centavos": 5000,
                "consumo_periodo": 2,
                "estoque_atual": 5,
                "lead_time_dias": 10,
                "lote_minimo": 1,
            },
            {
                "codigo": "TIE-MID",
                "descricao": "Empate Fronteira Meio",
                "categoria": "B",
                "unidade": "UN",
                "custo_unitario_centavos": 10000,
                "consumo_periodo": 1,
                "estoque_atual": 5,
                "lead_time_dias": 10,
                "lote_minimo": 1,
            },
        ]
        # Total = 100_000; A-TOP=70%, TIE-1=80% (A), TIE-MID=90% (B), TIE-2=100% (C)
        res = analisar(doc).to_dict()
        by_code = {it["codigo"]: it for it in res["itens"]}
        self.assertEqual(by_code["TIE-1"]["classe_abc"], "A")
        self.assertEqual(by_code["TIE-MID"]["classe_abc"], "B")
        self.assertEqual(by_code["TIE-2"]["classe_abc"], "C")
        for code in ("TIE-1", "TIE-MID", "TIE-2"):
            self.assertTrue(by_code[code]["empate_valor"])
            self.assertTrue(by_code[code]["empate_fronteira"])

    def test_value_tie_within_same_class_is_not_boundary_tie(self) -> None:
        doc = _base_doc()
        doc["itens"] = [
            {
                "codigo": "A-01",
                "descricao": "Item A1",
                "categoria": "Geral",
                "unidade": "UN",
                "custo_unitario_centavos": 40000,
                "consumo_periodo": 1,
                "estoque_atual": 5,
                "lead_time_dias": 10,
                "lote_minimo": 1,
            },
            {
                "codigo": "A-02",
                "descricao": "Item A2",
                "categoria": "Geral",
                "unidade": "UN",
                "custo_unitario_centavos": 40000,
                "consumo_periodo": 1,
                "estoque_atual": 5,
                "lead_time_dias": 10,
                "lote_minimo": 1,
            },
            {
                "codigo": "B-01",
                "descricao": "Item B1",
                "categoria": "Geral",
                "unidade": "UN",
                "custo_unitario_centavos": 15000,
                "consumo_periodo": 1,
                "estoque_atual": 5,
                "lead_time_dias": 10,
                "lote_minimo": 1,
            },
            {
                "codigo": "C-01",
                "descricao": "Item C1",
                "categoria": "Geral",
                "unidade": "UN",
                "custo_unitario_centavos": 5000,
                "consumo_periodo": 1,
                "estoque_atual": 5,
                "lead_time_dias": 10,
                "lote_minimo": 1,
            },
        ]
        res = analisar(doc).to_dict()
        by_code = {it["codigo"]: it for it in res["itens"]}
        self.assertTrue(by_code["A-01"]["empate_valor"])
        self.assertTrue(by_code["A-02"]["empate_valor"])
        self.assertFalse(by_code["A-01"]["empate_fronteira"])
        self.assertFalse(by_code["A-02"]["empate_fronteira"])

    def test_first_item_exceeding_limite_a_remains_class_a(self) -> None:
        doc = _base_doc()
        doc["itens"] = [
            {
                "codigo": "DOMINANTE",
                "descricao": "Item Dominante 90%",
                "categoria": "A",
                "unidade": "UN",
                "custo_unitario_centavos": 90000,
                "consumo_periodo": 1,
                "estoque_atual": 2,
                "lead_time_dias": 10,
                "lote_minimo": 1,
            },
            {
                "codigo": "SECUNDARIO",
                "descricao": "Item Secundário 10%",
                "categoria": "C",
                "unidade": "UN",
                "custo_unitario_centavos": 10000,
                "consumo_periodo": 1,
                "estoque_atual": 2,
                "lead_time_dias": 10,
                "lote_minimo": 1,
            },
        ]
        res = analisar(doc).to_dict()
        self.assertEqual(res["itens"][0]["classe_abc"], "A")
        self.assertEqual(res["itens"][1]["classe_abc"], "C")

    def test_replenishment_statuses_and_minimum_lot_ceiling(self) -> None:
        doc = _base_doc()
        res = analisar(doc)
        d = res.to_dict()
        by_code = {it["codigo"]: it for it in d["itens"]}

        # IT-A1: consumo=10 em 90d, LT=18 (dem_lt=2), seg=9 (est_seg=1), PP=3; disp=1 <= 1 -> critico; deficit=2; lote=2 -> pedido=2
        self.assertEqual(by_code["IT-A1"]["status_reposicao"], "critico")
        self.assertEqual(by_code["IT-A1"]["estoque_seguranca"], 1)
        self.assertEqual(by_code["IT-A1"]["ponto_pedido"], 3)
        self.assertEqual(by_code["IT-A1"]["deficit_unidades"], 2)
        self.assertEqual(by_code["IT-A1"]["lotes_sugeridos"], 1)
        self.assertEqual(by_code["IT-A1"]["pedido_sugerido_unidades"], 2)

        # IT-B1: disp=2, est_seg=1, PP=3 -> repor; deficit=1; lote_min=5 -> lotes=1, pedido=5
        self.assertEqual(by_code["IT-B1"]["status_reposicao"], "repor")
        self.assertEqual(by_code["IT-B1"]["deficit_unidades"], 1)
        self.assertEqual(by_code["IT-B1"]["lotes_sugeridos"], 1)
        self.assertEqual(by_code["IT-B1"]["pedido_sugerido_unidades"], 5)

        # IT-C1: disp=10 >= PP=2 -> adequado; pedido=0
        self.assertEqual(by_code["IT-C1"]["status_reposicao"], "adequado")
        self.assertEqual(by_code["IT-C1"]["pedido_sugerido_unidades"], 0)

        # IT-Z0: consumo=0 -> sem_consumo, cobertura=None, classe=C
        self.assertEqual(by_code["IT-Z0"]["status_reposicao"], "sem_consumo")
        self.assertEqual(by_code["IT-Z0"]["classe_abc"], "C")
        self.assertIsNone(by_code["IT-Z0"]["cobertura_dias_racional"])
        self.assertIsNone(res.coverage_days_fraction("IT-Z0"))

    def test_in_transit_stock_and_default_safety_days(self) -> None:
        doc = _base_doc()
        doc["itens"] = [
            {
                "codigo": "TR-01",
                "descricao": "Item Com Estoque em Trânsito",
                "categoria": "Geral",
                "unidade": "UN",
                "custo_unitario_centavos": 10000,
                "consumo_periodo": 30,
                "estoque_atual": 1,
                "estoque_em_transito": 5,
                "lead_time_dias": 9,
                "lote_minimo": 4,
            }
        ]
        # dias_seguranca omitido -> usa dias_seguranca_padrao=6 -> est_seg = ceil(30*6/90)=2, dem_lt=ceil(30*9/90)=3, PP=5
        # disp = 1 + 5 = 6 >= PP=5 -> adequado; cobertura = (6 * 90) / 30 = 18 dias
        res = analisar(doc)
        it = res.to_dict()["itens"][0]
        self.assertEqual(it["dias_seguranca"], 6)
        self.assertEqual(it["estoque_disponivel"], 6)
        self.assertEqual(it["ponto_pedido"], 5)
        self.assertEqual(it["status_reposicao"], "adequado")
        self.assertEqual(res.coverage_days_fraction("TR-01"), Fraction(18, 1))

    def test_result_immutability_and_unknown_code_lookup_error(self) -> None:
        doc = _base_doc()
        res = analisar(doc)
        d1 = res.to_dict()
        d1["resumo_geral"]["quantidade_itens"] = 999
        self.assertEqual(res.to_dict()["resumo_geral"]["quantidade_itens"], 4)

        s1 = res.source_document()
        s1["inventario"]["id"] = "alterado"
        self.assertEqual(res.source_document()["inventario"]["id"], "inv-teste-01")

        with self.assertRaises(EstoqueError) as ctx1:
            res.share_fraction("NAO-EXISTE")
        self.assertEqual(ctx1.exception.code, "INPUT_ID")
        with self.assertRaises(EstoqueError) as ctx2:
            res.cumulative_share_fraction("NAO-EXISTE")
        self.assertEqual(ctx2.exception.code, "INPUT_ID")
        with self.assertRaises(EstoqueError) as ctx3:
            res.coverage_days_fraction("NAO-EXISTE")
        self.assertEqual(ctx3.exception.code, "INPUT_ID")


class TestInputValidation(unittest.TestCase):
    def test_reject_non_dict_root_or_unknown_keys(self) -> None:
        with self.assertRaises(EstoqueError) as ctx:
            analisar(["lista"])
        self.assertEqual(ctx.exception.code, "INPUT_SCHEMA")

        doc = _base_doc()
        doc["extra"] = 1
        with self.assertRaises(EstoqueError) as ctx2:
            analisar(doc)
        self.assertEqual(ctx2.exception.code, "INPUT_SCHEMA")

    def test_reject_invalid_schema_version_or_bool_as_int(self) -> None:
        doc = _base_doc()
        doc["schema_version"] = 2
        with self.assertRaises(EstoqueError) as ctx:
            analisar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_SCHEMA")

        doc2 = _base_doc()
        doc2["schema_version"] = True
        with self.assertRaises(EstoqueError) as ctx2:
            analisar(doc2)
        self.assertEqual(ctx2.exception.code, "INPUT_SCHEMA")

    def test_reject_invalid_inventory_metadata(self) -> None:
        for field, bad_val, expected_code in (
            ("id", "invalido com espaco", "INPUT_ID"),
            ("titulo", "  Espaco Inicial", "INPUT_SCHEMA"),
            ("titulo", "Linha\nQuebrada", "INPUT_SCHEMA"),
            ("titulo", "", "INPUT_LIMIT"),
            ("data_referencia", "2026-02-30", "INPUT_DATE"),
            ("data_referencia", "31/03/2026", "INPUT_DATE"),
            ("moeda", "brl", "INPUT_SCHEMA"),
            ("moeda", "BR", "INPUT_SCHEMA"),
        ):
            doc = _base_doc()
            doc["inventario"][field] = bad_val  # type: ignore[index]
            with self.assertRaises(EstoqueError) as ctx:
                analisar(doc)
            self.assertEqual(ctx.exception.code, expected_code)

    def test_reject_invalid_abc_rational_thresholds(self) -> None:
        for lim_a, lim_ab in (
            ("0/1", "19/20"),
            ("4/5", "4/5"),
            ("19/20", "4/5"),
            ("4/5", "1/1"),
            ("4/5", "21/20"),
            ("04/5", "19/20"),
            ("00/5", "19/20"),
            ("80%", "95%"),
            (80, "19/20"),
        ):
            doc = _base_doc()
            doc["parametros"]["limite_a_racional"] = lim_a  # type: ignore[index]
            doc["parametros"]["limite_ab_racional"] = lim_ab  # type: ignore[index]
            with self.assertRaises(EstoqueError) as ctx:
                analisar(doc)
            self.assertEqual(ctx.exception.code, "INPUT_RATIONAL")

    def test_reject_duplicate_item_code_or_empty_items(self) -> None:
        doc = _base_doc()
        doc["itens"][1]["codigo"] = doc["itens"][0]["codigo"]  # type: ignore[index]
        with self.assertRaises(EstoqueError) as ctx:
            analisar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_ID")

        doc2 = _base_doc()
        doc2["itens"] = []
        with self.assertRaises(EstoqueError) as ctx2:
            analisar(doc2)
        self.assertEqual(ctx2.exception.code, "INPUT_LIMIT")

        doc3 = _base_doc()
        doc3["itens"] = "nao_lista"
        with self.assertRaises(EstoqueError) as ctx3:
            analisar(doc3)
        self.assertEqual(ctx3.exception.code, "INPUT_SCHEMA")

    def test_reject_out_of_range_numeric_parameters_and_item_fields(self) -> None:
        for mutator in (
            lambda d: d["parametros"].__setitem__("dias_analise", 0),
            lambda d: d["parametros"].__setitem__("dias_seguranca_padrao", -1),
            lambda d: d["itens"][0].__setitem__("custo_unitario_centavos", 0),
            lambda d: d["itens"][0].__setitem__("consumo_periodo", -1),
            lambda d: d["itens"][0].__setitem__("estoque_atual", -1),
            lambda d: d["itens"][0].__setitem__("estoque_em_transito", -1),
            lambda d: d["itens"][0].__setitem__("lead_time_dias", 0),
            lambda d: d["itens"][0].__setitem__("dias_seguranca", 400),
            lambda d: d["itens"][0].__setitem__("lote_minimo", 0),
        ):
            doc = _base_doc()
            mutator(doc)
            with self.assertRaises(EstoqueError) as ctx:
                analisar(doc)
            self.assertEqual(ctx.exception.code, "INPUT_LIMIT")

    def test_reject_all_zero_consumption_inventory(self) -> None:
        doc = _base_doc()
        for it in doc["itens"]:  # type: ignore[union-attr]
            it["consumo_periodo"] = 0
        with self.assertRaises(EstoqueError) as ctx:
            analisar(doc)
        self.assertEqual(ctx.exception.code, "DOMAIN_ZERO_TOTAL")


if __name__ == "__main__":
    unittest.main()
