from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from curva_abc_estoque.cli import main as cli_main
from tools.verify_independent import (
    CheckError,
    _synthetic_edge_scenario,
    check_bundle,
    load_json,
    main as verify_main,
)


def _repack(files: dict[str, bytes], target: Path) -> None:
    sha_lines = [
        f"{hashlib.sha256(files[n]).hexdigest()}  {n}\n"
        for n in sorted(files)
        if n != "SHA256SUMS"
    ]
    files["SHA256SUMS"] = "".join(sha_lines).encode("ascii")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as dst:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            dst.writestr(info, files[name])


class TestIndependentVerifier(unittest.TestCase):
    def test_verify_demo_bundle_passes_fraction_and_sqlite_oracles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "exemplo.zip"
            self.assertEqual(cli_main(["exemplo", "--saida", str(out)]), 0)
            report = check_bundle(out, demo=True)
            self.assertTrue(report["ok"])
            self.assertEqual(report["sqliteRowsVerified"], 15)
            self.assertEqual(report["fractionItemsVerified"], 15)

    def test_verify_synthetic_edge_scenario_passes_all_oracles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            edge_json = tmp_path / "borda.json"
            edge_bytes = (json.dumps(_synthetic_edge_scenario(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            edge_json.write_bytes(edge_bytes)
            edge_zip = tmp_path / "borda.zip"
            self.assertEqual(cli_main(["analisar", str(edge_json), "--saida", str(edge_zip)]), 0)
            report = check_bundle(edge_zip, demo=False, input_bytes=edge_bytes)
            self.assertTrue(report["ok"])
            self.assertEqual(report["sqliteRowsVerified"], 5)

    def test_verify_detects_tampered_sha256sums(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "exemplo.zip"
            cli_main(["exemplo", "--saida", str(out)])
            tampered = Path(tmp) / "tampered.zip"
            with zipfile.ZipFile(out) as src, zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_STORED) as dst:
                for info in src.infolist():
                    data = src.read(info.filename)
                    if info.filename == "LEIA-ME.md":
                        data = data + b"\nAlterado\n"
                    dst.writestr(info, data)
            with self.assertRaises(CheckError):
                check_bundle(tampered)

    def test_verify_detects_tampered_fraction_share_in_analise_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "exemplo.zip"
            cli_main(["exemplo", "--saida", str(out)])
            with zipfile.ZipFile(out) as src:
                files = {name: src.read(name) for name in src.namelist()}
            analise = json.loads(files["analise.json"].decode("utf-8"))
            analise["itens"][0]["participacao_racional"] = "1/3"
            files["analise.json"] = (json.dumps(analise, indent=2) + "\n").encode("utf-8")
            tampered = Path(tmp) / "tampered_share.zip"
            _repack(files, tampered)
            with self.assertRaises(CheckError):
                check_bundle(tampered)

    def test_verify_detects_tampered_boundary_tie_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "exemplo.zip"
            cli_main(["exemplo", "--saida", str(out)])
            with zipfile.ZipFile(out) as src:
                files = {name: src.read(name) for name in src.namelist()}
            analise = json.loads(files["analise.json"].decode("utf-8"))
            analise["itens"][3]["empate_fronteira"] = False
            files["analise.json"] = (json.dumps(analise, indent=2) + "\n").encode("utf-8")
            tampered = Path(tmp) / "tampered_tie.zip"
            _repack(files, tampered)
            with self.assertRaises(CheckError):
                check_bundle(tampered)

    def test_verify_detects_tampered_csv_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "exemplo.zip"
            cli_main(["exemplo", "--saida", str(out)])
            with zipfile.ZipFile(out) as src:
                files = {name: src.read(name) for name in src.namelist()}
            files["curva-abc.csv"] = files["curva-abc.csv"].replace(b"700000", b"699999")
            tampered = Path(tmp) / "tampered_csv.zip"
            _repack(files, tampered)
            with self.assertRaises(CheckError):
                check_bundle(tampered)

    def test_verify_detects_unsafe_svg_script_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "exemplo.zip"
            cli_main(["exemplo", "--saida", str(out)])
            with zipfile.ZipFile(out) as src:
                files = {name: src.read(name) for name in src.namelist()}
            files["curva-pareto.svg"] = files["curva-pareto.svg"].replace(b"</svg>", b"<script>alert(1)</script></svg>")
            tampered = Path(tmp) / "tampered_svg.zip"
            _repack(files, tampered)
            with self.assertRaises(CheckError):
                check_bundle(tampered)

    def test_independent_load_json_rejects_floats_bom_and_duplicates(self) -> None:
        for invalid in ('\ufeff{"a":1}', '{"a":1.5}', '{"a":NaN}', '{"a":1,"a":2}'):
            with self.assertRaises(CheckError):
                load_json(invalid)

    def test_verify_main_cli_entry_returns_zero_on_valid_bundle_and_zero_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "exemplo.zip"
            report_file = Path(tmp) / "relatorio-qa.json"
            cli_main(["exemplo", "--saida", str(out)])
            self.assertEqual(verify_main([str(out), "--demo", "--report", str(report_file)]), 0)
            self.assertTrue(report_file.is_file())
            self.assertEqual(verify_main([]), 0)
            self.assertEqual(verify_main([str(Path(tmp) / "inexistente.zip")]), 2)


if __name__ == "__main__":
    unittest.main()
