from __future__ import annotations

import csv
import io
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from curva_abc_estoque import EstoqueError, analisar, analisar_json, build_report, write_report
from curva_abc_estoque.jsoncodec import dumps, dumps_output, loads
from curva_abc_estoque.reports import REPORT_FILES, _safe_cell
from tests.test_core import _base_doc


class TestJsonCodec(unittest.TestCase):
    def test_reject_utf8_bom_in_str_and_bytes(self) -> None:
        with self.assertRaises(EstoqueError) as ctx:
            loads('\ufeff{"a": 1}')
        self.assertEqual(ctx.exception.code, "JSON_BOM")
        with self.assertRaises(EstoqueError) as ctx2:
            loads(b'\xef\xbb\xbf{"a": 1}')
        self.assertEqual(ctx2.exception.code, "JSON_BOM")

    def test_reject_floats_and_scientific_notation(self) -> None:
        for payload in ('{"a": 1.0}', '{"a": 0.5}', '{"a": 1e3}', '{"a": -2.5}'):
            with self.assertRaises(EstoqueError) as ctx:
                loads(payload)
            self.assertEqual(ctx.exception.code, "JSON_FLOAT")

    def test_reject_non_finite_constants(self) -> None:
        for payload in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}'):
            with self.assertRaises(EstoqueError) as ctx:
                loads(payload)
            self.assertEqual(ctx.exception.code, "JSON_INVALID")

    def test_reject_duplicate_keys(self) -> None:
        with self.assertRaises(EstoqueError) as ctx:
            loads('{"a": 1, "a": 2}')
        self.assertEqual(ctx.exception.code, "JSON_DUPLICATE_KEY")

    def test_reject_excessive_depth(self) -> None:
        deep = "[" * 18 + "]" * 18
        with self.assertRaises(EstoqueError) as ctx:
            loads(deep)
        self.assertEqual(ctx.exception.code, "JSON_LIMIT")

    def test_reject_oversized_integer_token(self) -> None:
        huge = '{"a": ' + ("9" * 45) + "}"
        with self.assertRaises(EstoqueError) as ctx:
            loads(huge)
        self.assertEqual(ctx.exception.code, "JSON_LIMIT")

    def test_reject_invalid_utf8_and_surrogates(self) -> None:
        with self.assertRaises(EstoqueError) as ctx:
            loads(b"\xff\xfe")
        self.assertEqual(ctx.exception.code, "JSON_UTF8")
        with self.assertRaises(EstoqueError) as ctx2:
            dumps({"a": "\ud800"})
        self.assertEqual(ctx2.exception.code, "JSON_INVALID")

    def test_reject_float_in_dumps(self) -> None:
        with self.assertRaises(EstoqueError) as ctx:
            dumps({"a": 1.5})
        self.assertEqual(ctx.exception.code, "JSON_INVALID")

    def test_reject_non_bytes_or_str_in_loads(self) -> None:
        with self.assertRaises(EstoqueError) as ctx:
            loads(123)  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.code, "JSON_INVALID")

    def test_roundtrip_primitives_and_empty_containers(self) -> None:
        sample = {"vazio_dict": {}, "vazio_list": [], "nulo": None, "ok": True, "falso": False, "zero": 0}
        encoded = dumps(sample)
        self.assertEqual(loads(encoded), sample)


class TestReportsAndBundle(unittest.TestCase):
    def test_build_report_contains_eight_deterministic_files(self) -> None:
        doc = _base_doc()
        raw = dumps(doc).encode("utf-8")
        res = analisar_json(raw)
        first = build_report(res, raw)
        second = build_report(res, raw)
        self.assertEqual(first, second)
        with zipfile.ZipFile(io.BytesIO(first)) as zf:
            self.assertEqual(tuple(zf.namelist()), REPORT_FILES)
            self.assertEqual(zf.read("entrada.json"), raw)

    def test_csv_formula_injection_protection_in_both_csvs(self) -> None:
        doc = _base_doc()
        doc["itens"][0]["descricao"] = "=SUM(1,2)"
        doc["itens"][0]["categoria"] = "+CMD|'/C calc'!A0"
        doc["itens"][1]["descricao"] = "@alerta_formula"
        doc["itens"][1]["categoria"] = "-subtracao"
        raw = dumps(doc).encode("utf-8")
        bundle = build_report(analisar(doc), raw)
        with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
            abc_rows = list(csv.DictReader(io.StringIO(zf.read("curva-abc.csv").decode("utf-8"))))
            rep_rows = list(csv.DictReader(io.StringIO(zf.read("reposicao.csv").decode("utf-8"))))
        self.assertEqual(abc_rows[0]["descricao"], "'=SUM(1,2)")
        self.assertEqual(abc_rows[0]["categoria"], "'+CMD|'/C calc'!A0")
        self.assertEqual(abc_rows[1]["descricao"], "'@alerta_formula")
        self.assertEqual(abc_rows[1]["categoria"], "'-subtracao")
        self.assertEqual(rep_rows[0]["descricao"], "'=SUM(1,2)")
        self.assertEqual(_safe_cell("  -100"), "'  -100")
        self.assertEqual(_safe_cell("\t=1"), "'\t=1")

    def test_svg_escapes_special_characters_and_xml_nonchars(self) -> None:
        doc = _base_doc()
        doc["inventario"]["titulo"] = 'Inventário <Teste> & "Aspas" \ufffe'
        doc["itens"][0]["descricao"] = "Peça <A&B> \uffff"
        raw = dumps_output(doc).encode("utf-8")
        bundle = build_report(analisar(doc), raw)
        with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
            svg_bytes = zf.read("curva-pareto.svg")
        root = ET.fromstring(svg_bytes)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")

    def test_svg_handles_more_than_fifteen_items_preserving_all_metadata_nodes(self) -> None:
        doc = _base_doc()
        doc["itens"] = [
            {
                "codigo": f"IT-{idx:02d}",
                "descricao": f"Componente {idx}",
                "categoria": "Geral",
                "unidade": "UN",
                "custo_unitario_centavos": (25 - idx) * 1000,
                "consumo_periodo": 5,
                "estoque_atual": 3,
                "lead_time_dias": 10,
                "lote_minimo": 1,
            }
            for idx in range(1, 19)
        ]
        raw = dumps(doc).encode("utf-8")
        bundle = build_report(analisar(doc), raw)
        with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
            root = ET.fromstring(zf.read("curva-pareto.svg"))
        codes = [n.get("data-codigo") for n in root.iter() if n.get("class") == "item-abc"]
        self.assertEqual(len(codes), 18)
        self.assertEqual(codes[0], "IT-01")
        self.assertEqual(codes[-1], "IT-18")

    def test_reject_mismatched_source_bytes_in_build_report(self) -> None:
        doc = _base_doc()
        res = analisar(doc)
        other = _base_doc()
        other["parametros"]["dias_seguranca_padrao"] = 10
        with self.assertRaises(EstoqueError) as ctx:
            build_report(res, dumps(other).encode("utf-8"))
        self.assertEqual(ctx.exception.code, "REPORT_SOURCE")
        with self.assertRaises(EstoqueError) as ctx2:
            build_report(res, "nao_bytes")  # type: ignore[arg-type]
        self.assertEqual(ctx2.exception.code, "REPORT_SOURCE")

    def test_write_report_atomic_and_refuses_existing_target_or_missing_parent(self) -> None:
        doc = _base_doc()
        raw = dumps(doc).encode("utf-8")
        res = analisar(doc)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "saida.zip"
            write_report(target, res, raw)
            original = target.read_bytes()
            with self.assertRaises(EstoqueError) as ctx:
                write_report(target, res, raw)
            self.assertEqual(ctx.exception.code, "OUTPUT_EXISTS")
            self.assertEqual(target.read_bytes(), original)

            missing_parent = Path(tmp) / "nao_existe" / "saida.zip"
            with self.assertRaises(EstoqueError) as ctx2:
                write_report(missing_parent, res, raw)
            self.assertEqual(ctx2.exception.code, "OUTPUT_PARENT")


if __name__ == "__main__":
    unittest.main()
