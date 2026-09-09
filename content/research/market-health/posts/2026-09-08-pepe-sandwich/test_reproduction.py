"""Regression tests for evidence-loss and stale-cache failures; no network."""
import contextlib
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import analyze
import collect
import evidence
import pack_evidence


def envelope(result):
    return {"endpoint": "https://example.invalid/rpc", "method": "eth_getLogs",
            "params": [], "retrieved_at_utc": "2026-09-09T00:00:00+00:00", "result": result}


class EvidenceRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.raw = self.data / "raw"
        self.raw.mkdir()
        self.archive = self.data / "evidence.jsonl.gz"
        self.manifest = self.data / "manifest.json"
        self.write_archive([{"name": "sample", "response": envelope(["old"])}])
        self.before = (self.archive.read_bytes(), self.manifest.read_bytes())
        analyze.archive.cache_clear()

    def tearDown(self):
        analyze.archive.cache_clear()
        self.temporary.cleanup()

    def write_archive(self, rows):
        self.archive.write_bytes(gzip.compress("".join(json.dumps(r) + "\n" for r in rows).encode(), mtime=0))
        self.manifest.write_text(json.dumps({"file": self.archive.name,
            "sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest(), "records": len(rows)}))

    def assert_preserved(self):
        self.assertEqual(self.before, (self.archive.read_bytes(), self.manifest.read_bytes()))
        self.assertEqual(list(self.data.glob(".evidence-*")), [])

    def test_pack_without_raw_leaves_archive_and_manifest_intact(self):
        with patch.object(pack_evidence, "DATA", self.data):
            with self.assertRaisesRegex(ValueError, "No raw evidence"):
                pack_evidence.main()
        self.assert_preserved()

    def test_pack_later_corrupt_record_leaves_originals_intact(self):
        (self.raw / "a.json.gz").write_bytes(gzip.compress(json.dumps(envelope([])).encode()))
        (self.raw / "z.json.gz").write_bytes(b"not gzip")
        with patch.object(pack_evidence, "DATA", self.data):
            with self.assertRaises(gzip.BadGzipFile):
                pack_evidence.main()
        self.assert_preserved()

    def test_pack_well_formed_but_incomplete_cache_is_rejected(self):
        (self.raw / "sample.json.gz").write_bytes(gzip.compress(json.dumps(envelope([])).encode()))
        with patch.object(pack_evidence, "DATA", self.data):
            with self.assertRaisesRegex(ValueError, "Missing required evidence record"):
                pack_evidence.main()
        self.assert_preserved()

    def test_archive_checksum_failure_precedes_parsing(self):
        self.archive.write_bytes(b"corrupted gzip")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            evidence.load_archive(self.data)

    def test_duplicate_names_rejected_even_with_matching_checksum(self):
        row = {"name": "sample", "response": envelope([])}
        self.write_archive([row, row])
        with self.assertRaisesRegex(ValueError, "duplicate archive record"):
            evidence.load_archive(self.data)

    def test_rpc_error_and_null_results_rejected(self):
        for response in (envelope(None), {**envelope([]), "error": {"message": "failed"}}):
            self.write_archive([{"name": "sample", "response": response}])
            with self.assertRaises(ValueError):
                evidence.load_archive(self.data)

    def test_missing_named_record_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "Missing required evidence record: missing"):
            evidence.named_result(evidence.load_archive(self.data), "missing")

    def test_complete_snapshot_with_wrong_identity_is_rejected(self):
        original = evidence.load_archive(Path(analyze.__file__).resolve().parent / "data")
        for name in ("identity-token0", "identity-token1", "identity-factory"):
            with self.subTest(name=name):
                records = dict(original)
                records[name] = {**records[name], "result": "0x" + "00"*32}
                with self.assertRaisesRegex(ValueError, "Unexpected pool identity: " + name):
                    analyze.preflight(lambda key: evidence.named_result(records, key))

    def test_raw_source_does_not_use_stale_archive(self):
        (self.raw / "sample.json.gz").write_bytes(gzip.compress(json.dumps(envelope(["new"])).encode()))
        with patch.object(analyze, "DATA", self.data), patch.object(analyze, "RAW", self.raw):
            with patch.object(analyze, "SOURCE", "archive"):
                self.assertEqual(analyze.raw("sample"), ["old"])
            with patch.object(analyze, "SOURCE", "raw"):
                self.assertEqual(analyze.raw("sample"), ["new"])
                with self.assertRaisesRegex(ValueError, "Missing required raw evidence"):
                    analyze.raw("missing")

    def test_collection_manifest_cannot_overwrite_archive_manifest(self):
        (self.raw / "sample.json.gz").write_bytes(gzip.compress(json.dumps(envelope([])).encode()))
        with patch.object(collect, "ROOT", self.root), patch.object(collect, "RAW", self.raw):
            collect.manifest()
        self.assert_preserved()
        self.assertTrue((self.data / "collection-manifest.json").exists())

    def test_transaction_collection_explicitly_selects_raw(self):
        def decode_raw():
            self.assertEqual(analyze.SOURCE, "raw")
            return [], {}
        with (patch.object(analyze, "SOURCE", "archive"),
              patch.object(analyze, "decode", side_effect=decode_raw),
              patch.object(collect, "manifest"),
              patch("sys.argv", ["collect.py", "transactions"]),
              contextlib.redirect_stdout(io.StringIO())):
            collect.main()


if __name__ == "__main__":
    unittest.main()
