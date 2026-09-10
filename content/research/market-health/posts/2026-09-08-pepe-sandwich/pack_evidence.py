#!/usr/bin/env python3
"""Pack unmodified RPC result envelopes into a single auditable offline snapshot."""
import collections
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import analyze
from evidence import checked_result, load_archive, named_result, publish_snapshot, verify_second_provider

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def validate_inventory(records, case):
    """Allow exactly the configured collection and actual replay dependencies."""
    expected = set()

    def read(name):
        expected.add(name)
        return named_result(records, name)

    analyze.preflight(read)
    # The collector retains the preceding boundary header for provenance even
    # though the reserve replay is initialized from initial-sync-logs.
    read(f"block-{analyze.START-1}")
    verify_second_provider(read, case)
    unexpected = sorted(set(records) - expected)
    if unexpected:
        raise ValueError(f"Unexpected evidence records ({len(unexpected)}): "
                         + ", ".join(unexpected[:10])
                         + "; use a clean raw cache for this collection")


def main():
    sources = sorted((DATA / "raw").glob("*.json.gz"))
    if not sources:
        raise ValueError("No raw evidence found; existing archive and manifest were left unchanged")
    counts, endpoints, times = collections.Counter(), collections.Counter(), []
    path = DATA / "evidence.jsonl.gz"
    # Any parse, validation or dependency failure leaves both originals intact.
    with tempfile.TemporaryDirectory(prefix=".evidence-", dir=DATA) as temporary:
        staging = Path(temporary)
        packed = staging / path.name
        with packed.open("wb") as output:
            with gzip.GzipFile(fileobj=output, mode="wb", mtime=0, filename="") as archive:
                for source in sources:
                    record = json.loads(gzip.decompress(source.read_bytes()))
                    name = source.name.removesuffix(".json.gz")
                    checked_result(name, record)
                    counts[record["method"]] += 1
                    endpoints[record["endpoint"]] += 1
                    times.append(record["retrieved_at_utc"])
                    row = {"name": name, "response": record}
                    archive.write((json.dumps(row, separators=(",", ":"))+"\n").encode())
        manifest = {"format": "gzip-compressed JSON Lines; one named RPC request/result envelope per line",
            "file": path.name, "sha256": hashlib.sha256(packed.read_bytes()).hexdigest(),
            "bytes": packed.stat().st_size, "records": sum(counts.values()), "methods": dict(counts),
            "endpoints": dict(endpoints), "first_retrieved_at_utc": min(times), "last_retrieved_at_utc": max(times)}
        staged_manifest = staging / "manifest.json"
        staged_manifest.write_text(json.dumps(manifest, indent=2)+"\n")
        # Validate actual replay semantics as well as the envelope/manifest
        # contract. A shape-correct but inconsistent receipt must not replace
        # the published archive. All derived files remain in staging.
        replay = subprocess.run([sys.executable, str(ROOT / "analyze.py"),
                                 "--source", "archive", "--data-dir", str(staging)],
                                capture_output=True, text=True)
        if replay.returncode:
            raise ValueError("Staged evidence is not replayable: " + replay.stderr[-2000:])
        records = load_archive(staging)
        case = json.loads((staging / "summary.json").read_text())["spot_check"]
        validate_inventory(records, case)
        manifest = publish_snapshot(staging, DATA)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
