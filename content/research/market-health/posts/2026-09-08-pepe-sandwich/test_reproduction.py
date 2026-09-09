"""Regression tests for evidence-loss and stale-cache failures; no network."""
import contextlib
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import shutil
import subprocess
import sys
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

    def test_collector_rejects_method_specific_incomplete_results(self):
        for method, result in [("eth_getBlockByNumber", {"hash": "0x1"}),
                               ("eth_getTransactionByHash", {"hash": "0x1"}),
                               ("eth_getTransactionReceipt", {"transactionHash": "0x1"}),
                               ("eth_call", "0x")]:
            with self.subTest(method=method):
                self.assertFalse(collect.valid(method, result))

    def test_second_provider_requires_records_and_matching_gas_price(self):
        case = {"front": "a", "victim": "b", "back": "c"}
        receipt = {key: ([] if key == "logs" else "0x1") for key in evidence.RECEIPT_FIELDS}
        records = {prefix + tx: {"method": "eth_getTransactionReceipt", "result": dict(receipt)}
                   for prefix in ("receipt-", "verify-receipt-") for tx in case.values()}
        def read(name):
            return evidence.named_result(records, name)
        evidence.verify_second_provider(read, case)
        records["verify-receipt-a"]["result"]["effectiveGasPrice"] = "0x2"
        with self.assertRaisesRegex(ValueError, "effectiveGasPrice"):
            evidence.verify_second_provider(read, case)
        del records["verify-receipt-a"]
        with self.assertRaisesRegex(ValueError, "Missing required evidence record"):
            evidence.verify_second_provider(read, case)

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
              patch.object(analyze, "decode", side_effect=decode_raw) as decode,
              patch.object(collect, "manifest"),
              patch("sys.argv", ["collect.py", "transactions"]),
              contextlib.redirect_stdout(io.StringIO())):
            collect.main()
            decode.assert_called_once_with()

    def test_corrupt_cache_is_refetched_and_valid_cache_reused(self):
        path = self.raw / "sample.json.gz"
        broken = [b"not gzip", gzip.compress(b"{") , gzip.compress(b"[]"),
                  gzip.compress(b"\xff"), gzip.compress(b"{}")[:-4]]
        for blob in broken:
            with self.subTest(blob=blob):
                path.write_bytes(blob)
                response = contextlib.nullcontext(io.BytesIO(b'{"result": ["fresh"]}'))
                with (patch.object(collect, "RAW", self.raw),
                      patch.object(collect.urllib.request, "urlopen", return_value=response) as fetch):
                    self.assertEqual(collect.fetch("sample", "eth_getLogs", []), ["fresh"])
                    fetch.assert_called_once()
                with (patch.object(collect, "RAW", self.raw),
                      patch.object(collect.urllib.request, "urlopen", side_effect=RuntimeError("Network forbidden"))):
                    self.assertEqual(collect.fetch("sample", "eth_getLogs", []), ["fresh"])

    def staged_snapshot(self):
        staging = self.root / "staging"
        staging.mkdir()
        blob = gzip.compress((json.dumps({"name": "sample", "response": envelope(["new"])}) + "\n").encode())
        (staging / self.archive.name).write_bytes(blob)
        (staging / "manifest.json").write_text(json.dumps({"file": self.archive.name,
            "sha256": hashlib.sha256(blob).hexdigest(), "records": 1}))
        return staging

    def test_incomplete_cache_metadata_is_refetched_before_manifest(self):
        original = {**envelope(["stale"]), "endpoint": collect.OTHER_RPC}
        variants = []
        for key in ("endpoint", "method", "params", "retrieved_at_utc"):
            incomplete = dict(original)
            del incomplete[key]
            variants.append(("missing " + key, incomplete))
        for value in (None, "", 123, "not-a-date", "2026-09-09T00:00:00",
                      "2026-09-09T03:00:00+03:00"):
            variants.append((repr(value), {**original, "retrieved_at_utc": value}))
        for label, cached in variants:
            with self.subTest(metadata=label):
                path = self.raw / "sample.json.gz"
                path.write_bytes(gzip.compress(json.dumps(cached).encode()))
                response = contextlib.nullcontext(io.BytesIO(b'{"result": ["fresh"]}'))
                with (patch.object(collect, "ROOT", self.root),
                      patch.object(collect, "RAW", self.raw),
                      patch.object(collect.urllib.request, "urlopen", return_value=response) as fetch):
                    self.assertEqual(collect.fetch("sample", "eth_getLogs", []), ["fresh"])
                    fetch.assert_called_once()
                    collect.manifest()
                    repaired = json.loads(gzip.decompress(path.read_bytes()))
                    self.assertTrue(collect.valid_retrieval_time(repaired["retrieved_at_utc"]))
                with (patch.object(collect, "RAW", self.raw),
                      patch.object(collect.urllib.request, "urlopen", side_effect=RuntimeError("Network forbidden"))):
                    self.assertEqual(collect.fetch("sample", "eth_getLogs", []), ["fresh"])
        self.assert_preserved()

    def test_interruption_before_manifest_switch_preserves_old_snapshot(self):
        staging = self.staged_snapshot()
        original_replace = Path.replace

        class Interrupted(BaseException):
            pass

        def stop_before_switch(path, target):
            if Path(target) == self.manifest:
                raise Interrupted()
            return original_replace(path, target)

        with patch.object(Path, "replace", stop_before_switch):
            with self.assertRaises(Interrupted):
                evidence.publish_snapshot(staging, self.data)
        self.assert_preserved()
        self.assertEqual(evidence.named_result(evidence.load_archive(self.data), "sample"), ["old"])

    def test_new_publication_preserves_prior_archive_and_switches_reader(self):
        staging = self.staged_snapshot()
        published = evidence.publish_snapshot(staging, self.data)
        self.assertNotEqual(published["file"], self.archive.name)
        self.assertEqual(self.archive.read_bytes(), self.before[0])
        self.assertEqual(evidence.named_result(evidence.load_archive(self.data), "sample"), ["new"])

    def test_optimized_python_rejects_corrupt_derived_output(self):
        source = Path(analyze.__file__).resolve().parent
        copied = self.root / "optimized-data"
        shutil.copytree(source / "data", copied, ignore=shutil.ignore_patterns("raw"))
        victims = copied / "victims.csv"
        with victims.open() as stream:
            rows = list(csv.DictReader(stream))
        rows[0]["counterfactual_output_raw"] = str(int(rows[0]["counterfactual_output_raw"]) + 1)
        with victims.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        validation_before = (copied / "validation.json").read_bytes()
        program = ("from pathlib import Path; import analyze, validate; "
                   f"analyze.DATA = Path({str(copied)!r}); validate.main()")
        result = subprocess.run([sys.executable, "-O", "-c", program],
                                cwd=source, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ValueError: Validation failed: output ==", result.stderr)
        self.assertEqual((copied / "validation.json").read_bytes(), validation_before)


if __name__ == "__main__":
    unittest.main()
