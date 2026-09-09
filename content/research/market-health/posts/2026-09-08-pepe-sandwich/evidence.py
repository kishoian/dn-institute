"""Validate saved RPC envelopes before any replay or archive replacement."""
import gzip
import hashlib
import json
from pathlib import Path


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
        valid = isinstance(result, dict) and all(k in result for k in ("hash", "timestamp"))
    elif method == "eth_getTransactionByHash":
        valid = isinstance(result, dict) and "hash" in result
    elif method == "eth_getTransactionReceipt":
        valid = (isinstance(result, dict) and "transactionHash" in result
                 and isinstance(result.get("logs"), list) and "status" in result)
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
