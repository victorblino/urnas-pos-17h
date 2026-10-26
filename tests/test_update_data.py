import base64
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from update_data import aggregate_state, infer_offset, local_archives, parse_log, portal_snapshot  # noqa: E402
from download_tse_logs import (  # noqa: E402
    Section,
    decode_jws,
    download_section,
    log_from_auxiliary,
    log_url,
    sections_from_index,
)


def packed_log(lines: list[str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("logd.dat", "\n".join(lines).encode("latin-1"))
    return output.getvalue()


class UpdateDataTest(unittest.TestCase):
    def test_portal_snapshot_changes_for_new_or_changed_log(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            directory = Path(temporary)
            log = directory / "o03221ac0000000000000-log.jez"
            log.write_bytes(b"first")
            first = portal_snapshot(directory)
            log.write_bytes(b"second")
            second = portal_snapshot(directory)
            (directory / "o03221ac0000000000001-log.jez").write_bytes(b"third")
            third = portal_snapshot(directory)
        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)

    def test_acre_cutoff_is_1503_local(self):
        content = packed_log(
            [
                "25/10/2026 06:00:00\tINFO\t1\tVOTA\tAguardando digitação do identificador do eleitor",
                "25/10/2026 15:02:59\tINFO\t1\tVOTA\tO voto do eleitor foi computado",
                "25/10/2026 15:03:00\tINFO\t1\tVOTA\tO voto do eleitor foi computado",
            ]
        )
        result = parse_log("o03221ac0106600040001-log.jez", content, "AC")
        self.assertIsNotNone(result)
        self.assertEqual(result.inferred_offset, -2)
        self.assertEqual(result.after_decision_by_offset[-2], 1)
        state, sections = aggregate_state("AC", [result])
        self.assertEqual(sections[0]["beforeDecision"], 1)
        self.assertEqual(sections[0]["afterDecision"], 1)
        self.assertEqual(sections[0]["lastVoteAtLocal"], "15:03:00")
        self.assertEqual(sections[0]["lastVoteAtBrasilia"], "17:03:00")
        self.assertEqual(state["beforeDecision"], 1)

    def test_non_election_day_is_ignored(self):
        content = packed_log(
            [
                "04/10/2026 18:00:00\tINFO\t1\tVOTA\tO voto do eleitor foi computado",
                "25/10/2026 08:00:00\tINFO\t1\tVOTA\tAguardando digitação do identificador do eleitor",
            ]
        )
        result = parse_log("o03221sp7107200010001-log.jez", content, "SP")
        self.assertEqual(result.votes, 0)

    def test_second_round_closing_is_normalized_to_brasilia(self):
        content = packed_log(
            [
                "25/10/2026 06:00:00\tINFO\t1\tVOTA\tAguardando digitação do identificador do eleitor",
                "25/10/2026 14:59:59\tINFO\t1\tVOTA\tInicio do Encerramento",
            ]
        )
        result = parse_log("o03221ac0106600040001-log.jez", content, "AC")
        self.assertEqual(result.closing, 14 * 3600 + 59 * 60 + 59)
        state, sections = aggregate_state("AC", [result])
        self.assertEqual(sections[0]["closingStartedAtBrasilia"], "16:59:59")
        self.assertEqual(state["sectionsClosedBeforeDecision"], 1)
        self.assertEqual(state["earliestClosingStartedAtBrasilia"], "16:59:59")
        self.assertEqual(state["latestClosingStartedAtBrasilia"], "16:59:59")

    def test_first_round_log_is_rejected(self):
        content = packed_log(
            ["25/10/2026 18:00:00\tINFO\t1\tVOTA\tO voto do eleitor foi computado"]
        )
        self.assertIsNone(parse_log("o03220sp7107200010001-log.jez", content, "SP"))

    def test_decision_cutoff_is_1703_brasilia(self):
        content = packed_log(
            [
                "25/10/2026 08:00:00\tINFO\t1\tVOTA\tAguardando digitação do identificador do eleitor",
                "25/10/2026 17:03:00\tINFO\t1\tVOTA\tInicio do Encerramento",
            ]
        )
        result = parse_log("o03221sp7107200010001-log.jez", content, "SP")
        state, _ = aggregate_state("SP", [result])
        self.assertEqual(state["sectionsClosedBeforeDecision"], 0)

    def test_missing_opening_uses_state_offset(self):
        self.assertEqual(infer_offset(None, "AM"), -1)
        self.assertEqual(infer_offset(None, "SP"), 0)

    def test_local_archives_only_accept_second_turn(self):
        directory = Mock()
        directory.glob.return_value = [
            Path("bu_imgbu_logjez_rdv_vscmr_2026_2t_SP.zip"),
            Path("bu_imgbu_logjez_rdv_vscmr_2026_1t_SP.zip"),
        ]
        self.assertEqual(
            local_archives(directory),
            [("SP", Path("bu_imgbu_logjez_rdv_vscmr_2026_2t_SP.zip"))],
        )

    def test_portal_index_keeps_every_section_in_second_round_process(self):
        data = {
            "cdp": "3221",
            "dg": "25/10/2026",
            "abr": [{"cd": "sp", "mu": [{"cd": "62499", "zon": [{"cd": "0026", "sec": [
                {"ns": "0370", "da": "25/10/2026"},
                {"ns": "0371", "da": "26/10/2026"},
                {"ns": "0372", "da": None},
            ]}]}]}],
        }
        self.assertEqual(
            sections_from_index(data, "sp"),
            [Section("sp", "62499", "0026", "0370"), Section("sp", "62499", "0026", "0371")],
        )

    def test_portal_manifest_builds_official_log_url(self):
        auxiliary = {
            "hashes": [{"hash": "abc123", "dr": "25/10/2026", "hr": "20:17:55", "arq": [
                {"nm": "o03221sp6249900260370-bu.dat", "tp": "bu"},
                {"nm": "o03221sp6249900260370-log.jez", "tp": "log"},
            ]}],
        }
        item = Section("sp", "62499", "0026", "0370")
        hash_value, filename = log_from_auxiliary(auxiliary)
        self.assertEqual(
            log_url(item, hash_value, filename),
            "https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3221/dados/sp/62499/0026/0370/abc123/o03221sp6249900260370-log.jez",
        )

    def test_existing_log_is_skipped_without_network_request(self):
        item = Section("ac", "01007", "0009", "0001")
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            output = Path(directory)
            destination = output / "ac" / "01007" / "0009" / "0001" / "o03221ac0100700090001-log.jez"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"existing")
            with patch("download_tse_logs.fetch_jws") as fetch:
                result = download_section(item, output)
            self.assertEqual(result["status"], "skipped")
            fetch.assert_not_called()

    def test_decode_jws_payload(self):
        payload = base64.urlsafe_b64encode(b'{"cdp":"3221"}').rstrip(b"=")
        self.assertEqual(decode_jws(b"header." + payload + b".signature"), {"cdp": "3221"})


if __name__ == "__main__":
    unittest.main()
