"""Validate saved RPC envelopes before any replay or archive replacement."""
import gzip
import hashlib
import json
from pathlib import Path


def require(condition, message):
    """Keep evidence checks active under python -O and PYTHONOPTIMIZE."""
    if not condition:
        raise ValueError(message)


RECEIPT_FIELDS = ("transactionHash", "blockHash", "blockNumber", "transactionIndex",
                  "gasUsed", "effectiveGasPrice", "status", "logs")


def compare_receipts(first, second, label):
    for key in RECEIPT_FIELDS:
        require(key in first and key in second and first[key] == second[key],
                f"Second-provider receipt mismatch: {label}: {key}")


def verify_second_provider(read, case):
    for role in ("front", "victim", "back"):
        tx = case[role]
        compare_receipts(read("receipt-" + tx), read("verify-receipt-" + tx), role)


def publish_snapshot(staging, data):
    """Publish immutable evidence, then atomically switch the manifest pointer.

    Older archives remain readable. An interruption before the manifest switch
    can leave an unreferenced new archive, but cannot invalidate the old pair.
    """
    manifest_path = staging / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    packed = staging / manifest["file"]
    blob = packed.read_bytes()
    require(hashlib.sha256(blob).hexdigest() == manifest["sha256"],
            "Staged archive checksum mismatch before publication")
    filename = f"evidence-{manifest['sha256']}.jsonl.gz"
    current_path = data / "manifest.json"
    if current_path.exists():
        current = json.loads(current_path.read_text())
        previous = current.get("file", "")
        require(bool(previous) and Path(previous).name == previous,
                "Current manifest must name a file in its own directory")
        # Preserve the legacy filename and byte-identical manifest on a no-op
        # repack; do not overwrite an archive referenced by an older manifest.
        if current.get("sha256") == manifest["sha256"]:
            require((data / previous).read_bytes() == blob,
                    "Current archive differs from its declared checksum")
            filename = previous
    destination = data / filename
    if destination.exists():
        require(destination.read_bytes() == blob,
                "Refusing to overwrite a different immutable evidence archive")
    else:
        packed.replace(destination)
    manifest["file"] = filename
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    manifest_path.replace(current_path)
    return manifest


def checked_result(name, envelope):
    if not isinstance(envelope, dict) or "error" in envelope:
        raise ValueError(f"Invalid RPC envelope for {name}")
    method, result = envelope.get("method"), envelope.get("result")
    valid = False
    if method == "eth_getLogs":
        valid = isinstance(result, list)
    elif method == "eth_call":
        valid = isinstance(result, str) and result.startswith("0x") and len(result) > 2
    elif method == "eth_getBlockByNumber":
        valid = isinstance(result, dict) and all(result.get(k) is not None for k in ("hash", "timestamp", "number"))
    elif method == "eth_getTransactionByHash":
        valid = (isinstance(result, dict) and all(result.get(k) is not None
                 for k in ("hash", "blockHash", "blockNumber", "transactionIndex", "from")) and "to" in result)
    elif method == "eth_getTransactionReceipt":
        valid = (isinstance(result, dict) and all(result.get(k) is not None for k in RECEIPT_FIELDS)
                 and isinstance(result.get("logs"), list))
    if not valid:
        raise ValueError(f"Missing or unusable {method!r} result for {name}")
    return result


def load_archive(data, manifest_name="manifest.json"):
    manifest = json.loads((data / manifest_name).read_text())
    if not isinstance(manifest, dict):
        raise ValueError(f"{manifest_name} must be an archive manifest object")
    filename = manifest.get("file", "")
    if not filename or Path(filename).name != filename:
        raise ValueError("Archive manifest must name a file in its own directory")
    path = data / filename
    blob = path.read_bytes()
    if hashlib.sha256(blob).hexdigest() != manifest.get("sha256"):
        raise ValueError(f"Archive checksum mismatch: {filename}")
    if "bytes" in manifest and len(blob) != manifest["bytes"]:
        raise ValueError(f"Archive byte count mismatch: {filename}")
    records = {}
    with gzip.open(path, "rt") as stream:
        for line in stream:
            row = json.loads(line)
            name = row.get("name")
            if not isinstance(name, str) or not name or name in records:
                raise ValueError(f"Missing or duplicate archive record name: {name!r}")
            checked_result(name, row.get("response"))
            records[name] = row["response"]
    if len(records) != manifest.get("records"):
        raise ValueError(f"Archive record count mismatch: {filename}")
    return records


def named_result(records, name):
    if name not in records:
        raise ValueError(f"Missing required evidence record: {name}")
    return checked_result(name, records[name])
