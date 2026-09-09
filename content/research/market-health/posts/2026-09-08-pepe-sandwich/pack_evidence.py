#!/usr/bin/env python3
"""Pack unmodified RPC result envelopes into a single auditable offline snapshot."""
import collections
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import analyze
from evidence import checked_result, load_archive, named_result

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def main():
    sources = sorted((DATA / "raw").glob("*.json.gz"))
    if not sources:
        raise ValueError("No raw evidence found; existing archive and manifest were left unchanged")
    counts, endpoints, times = collections.Counter(), collections.Counter(), []
    path = DATA / "evidence.jsonl.gz"
    # Same-filesystem temporary files allow atomic replacement of each file.
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
        records = load_archive(staging)
        analyze.preflight(lambda name: named_result(records, name))
        packed.replace(path)
        staged_manifest.replace(DATA / "manifest.json")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
