#!/usr/bin/env python3
"""Pack unmodified RPC result envelopes into a single auditable offline snapshot."""
import collections
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def main():
    counts, endpoints, times = collections.Counter(), collections.Counter(), []
    path = DATA / "evidence.jsonl.gz"
    with path.open("wb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", mtime=0, filename="") as archive:
            for source in sorted((DATA / "raw").glob("*.json.gz")):
                record = json.loads(gzip.decompress(source.read_bytes()))
                counts[record["method"]] += 1
                endpoints[record["endpoint"]] += 1
                times.append(record["retrieved_at_utc"])
                row = {"name": source.name.removesuffix(".json.gz"), "response": record}
                archive.write((json.dumps(row, separators=(",", ":"))+"\n").encode())
    manifest = {"format": "gzip-compressed JSON Lines; one named RPC request/result envelope per line",
        "file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size, "records": sum(counts.values()), "methods": dict(counts),
        "endpoints": dict(endpoints), "first_retrieved_at_utc": min(times), "last_retrieved_at_utc": max(times)}
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
