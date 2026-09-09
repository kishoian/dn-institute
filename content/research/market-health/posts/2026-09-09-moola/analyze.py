"""Offline reconstruction of pool pressure, MOO collateral and actual loan quotes.

The seed list is a discovery aid only. Main-account coverage comes from its
complete normal-transaction query, and the pool sample includes all log pages.
"""

import collections
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import zipfile
from decimal import Decimal, getcontext
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

getcontext().prec = 60
ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
OUT = ROOT / "analysis"
MOO = "0x17700282592d6917f6a73d0bf8accf4d578c131e"
MCELO = "0x7d00cd74ff385c955ea3d79e47bf06bd7386387d"
CELO = "0x471ece3750da237f93b8e339c536989b8978a438"
MAIN = "0x5dae2c3d5a9f35bfaf36a2e6edd07c477f57789e"
POOL = "0x9272388fdf2d6bfba8b1cdd99732a3d552a71346"
LENDING = "0x970b12522ca9b4054807a2c5b736149a5be6f670"
ORACLE = "0xba2224905ad3cdba6c1b764cd62fda52bd524d29"
FEED = "0xe8e30f32141321180cc1827de43d841f4e88b968"
SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
SYNC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
BORROW = "0xc6a898309e823ee50bac64e45ca8adba6690e99e7841c45d754e2a38e9019d9b"
DEPOSIT = "0xde6857219544bb5b7746f48ed30be6386fefc61b2f864cacf559893bf50fd951"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
START, END, CUTOFF = 15670000, 15695000, 1666110324
UNIT = Decimal(10**18)
ASSETS = {
    MOO: "MOO",
    CELO: "CELO",
    MCELO: "mCELO",
    "0x765de816845861e75a25fca122bb6898b8b1282a": "cUSD",
    "0xd8763cba276a3738e6de85b4b3bf5fded6d6ca73": "cEUR",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


@lru_cache(maxsize=1)
def sources():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    require(Path(manifest["file"]).name == manifest["file"], "Unsafe snapshot filename")
    blob = (ROOT / manifest["file"]).read_bytes()
    require(
        hashlib.sha256(blob).hexdigest() == manifest["sha256"]
        and len(blob) == manifest["bytes"],
        "Snapshot checksum or size mismatch",
    )
    result = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = [x["file"] for x in manifest["files"]]
        require(
            len(names) == len(set(names))
            and sorted(archive.namelist()) == sorted(names),
            "Snapshot entry mismatch",
        )
        for item in manifest["files"]:
            path = Path(item["file"])
            require(
                not path.is_absolute()
                and path.parent == Path("raw")
                and path.name.endswith(".json.gz"),
                "Unsafe snapshot entry name",
            )
            saved = archive.read(item["file"])
            require(
                hashlib.sha256(saved).hexdigest() == item["sha256"],
                "Source envelope checksum mismatch",
            )
            result[path.name[:-8]] = json.loads(gzip.decompress(saved))
    return result


def raw(name):
    return sources()[name]["response"]


def words(value):
    require(
        value.startswith("0x") and (len(value) - 2) % 64 == 0,
        "Invalid ABI word sequence",
    )
    return [int(value[i : i + 64], 16) for i in range(2, len(value), 64)]


def address(value):
    return "0x" + value[-40:].lower()


def stamp(value):
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def utc(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def logs(tx):
    result, expected = [], None
    for page in range(100):
        envelope = sources()[f"logs-{tx}-{page}"]
        response = envelope["response"]
        result.extend(response["items"])
        expected = response["next_page_params"]
        if not expected:
            break
    require(not expected, "Incomplete transaction log pagination")
    require(
        len(result) == len({x["index"] for x in result}), "Duplicate transaction log"
    )
    require(
        all(x["transaction_hash"].lower() == tx for x in result),
        "Wrong log transaction",
    )
    return result


def calls(trace):
    yield trace
    for child in trace.get("calls", []):
        yield from calls(child)


def pool_events(start, end, leaves):
    response = raw(f"pool-{start}-{end}")
    rows = response["result"]
    require(isinstance(rows, list), "Invalid pool log page")
    if len(rows) >= 1000:
        require(start < end, "Single-block log cap prevents complete sampling")
        middle = (start + end) // 2
        return pool_events(start, middle, leaves) + pool_events(middle + 1, end, leaves)
    require(
        all(
            start <= int(x["blockNumber"], 16) <= end and x["address"].lower() == POOL
            for x in rows
        ),
        "Pool log outside requested block/address range",
    )
    leaves.append({"start": start, "end": end, "events": len(rows)})
    return rows


def write_csv(name, rows):
    require(bool(rows), "Cannot silently publish an empty ledger")
    with (OUT / name).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    sources()
    OUT.mkdir(exist_ok=True)
    identity = sources()["pool-identity"]
    require(
        {x["id"]: address(x["result"]) for x in identity["response"]}
        == {1: MOO, 2: MCELO},
        "Unexpected pool token ordering",
    )
    decimals = raw("token-decimals")
    require(
        len(decimals) == 5 and all(words(x["result"]) == [18] for x in decimals),
        "Unexpected decimals",
    )
    history = raw("actor-transactions")["result"]
    require(isinstance(history, list) and len(history) < 1000, "Capped account history")
    outgoing = [x for x in history if x["from"].lower() == MAIN]
    require(
        sorted(int(x["nonce"]) for x in outgoing) == list(range(67)),
        "Main-account nonce gap",
    )
    focus = [x for x in outgoing if int(x["timeStamp"]) <= CUTOFF]
    focus_hashes = {x["hash"].lower() for x in focus}
    account_rows = []
    for x in focus:
        tx = x["hash"].lower()
        metadata = raw("tx-" + tx)
        require(
            metadata["from"]["hash"].lower() == MAIN and metadata["hash"].lower() == tx,
            "Transaction/account mismatch",
        )
        require(
            metadata["block_number"] == int(x["blockNumber"])
            and stamp(metadata["timestamp"]) == int(x["timeStamp"]),
            "Transaction/account time mismatch",
        )
        require(
            metadata["raw_input"].lower() == x["input"].lower(),
            "Transaction/account calldata mismatch",
        )
        require(
            (metadata["status"] == "ok") == (x["isError"] == "0"),
            "Transaction status mismatch",
        )
        account_rows.append(
            {
                "tx": tx,
                "nonce": int(x["nonce"]),
                "timestamp": int(x["timeStamp"]),
                "timestamp_utc": utc(int(x["timeStamp"])),
                "method": metadata["method"],
                "status": metadata["status"],
                "block": metadata["block_number"],
            }
        )
        if metadata["status"] != "ok":
            require(not logs(tx), "Failed transaction emitted logs")

    leaves = []
    events = pool_events(START, END, leaves)
    events.sort(key=lambda x: (int(x["blockNumber"], 16), int(x["logIndex"], 16)))
    require(
        len(events) == len({(x["transactionHash"], x["logIndex"]) for x in events}),
        "Duplicate pool event",
    )
    require(
        all(a["end"] + 1 == b["start"] for a, b in pairwise(leaves)),
        "Pool range gap",
    )
    swaps = []
    reserve_states = []
    sync = None
    for x in events:
        if x["topics"][0] == SYNC:
            sync = x
            r0, r1 = words(x["data"])
            require(r0 > 0 and r1 > 0, "Nonpositive synchronized reserves")
            reserve_states.append(
                {
                    "tx": x["transactionHash"],
                    "block": int(x["blockNumber"], 16),
                    "log_index": int(x["logIndex"], 16),
                    "timestamp": int(x["timeStamp"], 16),
                    "timestamp_utc": utc(int(x["timeStamp"], 16)),
                    "MOO_raw": r0,
                    "mCELO_raw": r1,
                    "mCELO_per_MOO": str(Decimal(r1) / Decimal(r0)),
                }
            )
        elif x["topics"][0] == SWAP:
            tx = x["transactionHash"].lower()
            require(
                sync is not None
                and sync["transactionHash"].lower() == tx
                and int(sync["logIndex"], 16) + 1 == int(x["logIndex"], 16),
                "Missing adjacent Sync",
            )
            a0i, a1i, a0o, a1o = words(x["data"])
            r0, r1 = words(sync["data"])
            p0, p1 = r0 - a0i + a0o, r1 - a1i + a1o
            require(min(r0, r1, p0, p1) > 0, "Nonpositive pool balances")
            net0, net1 = a0i - a0o, a1i - a1o
            direction = (
                "buy_MOO"
                if net0 < 0 < net1
                else "sell_MOO"
                if net1 < 0 < net0
                else "complex"
            )
            swaps.append(
                {
                    "tx": tx,
                    "block": int(x["blockNumber"], 16),
                    "log_index": int(x["logIndex"], 16),
                    "timestamp": int(x["timeStamp"], 16),
                    "timestamp_utc": utc(int(x["timeStamp"], 16)),
                    "main_account_transaction": tx in focus_hashes,
                    "direction": direction,
                    "a0in": a0i,
                    "a1in": a1i,
                    "a0out": a0o,
                    "a1out": a1o,
                    "net_MOO_to_pool": str(Decimal(net0) / UNIT),
                    "net_mCELO_to_pool": str(Decimal(net1) / UNIT),
                    "pre0": p0,
                    "pre1": p1,
                    "post0": r0,
                    "post1": r1,
                    "pre_mCELO_per_MOO": str(Decimal(p1) / Decimal(p0)),
                    "post_mCELO_per_MOO": str(Decimal(r1) / Decimal(r0)),
                }
            )
    main_swaps = [x for x in swaps if x["main_account_transaction"]]
    require(
        main_swaps and all(x["direction"] == "buy_MOO" for x in main_swaps),
        "Unexpected main-account pool direction",
    )
    for x in main_swaps:
        matching = [
            y
            for y in logs(x["tx"])
            if y["address"]["hash"].lower() == POOL and y["index"] == x["log_index"]
        ]
        require(
            len(matching) == 1
            and words(matching[0]["data"])
            == [x[k] for k in ["a0in", "a1in", "a0out", "a1out"]],
            "Pool feed / transaction log mismatch",
        )

    borrows = []
    deposits = []
    all_transactions = {
        name[3:]: raw(name) for name in sources() if name.startswith("tx-")
    }
    for tx, meta in all_transactions.items():
        if meta["status"] != "ok":
            continue
        for event in logs(tx):
            if event["address"]["hash"].lower() != LENDING or event["topics"][
                0
            ] not in [BORROW, DEPOSIT]:
                continue
            is_borrow = event["topics"][0] == BORROW
            data = words(event["data"])
            require(
                len(data) == (4 if is_borrow else 2), "Unexpected lending event size"
            )
            asset = address(event["topics"][1])
            beneficiary = address(event["topics"][2])
            user = "0x" + f"{data[0]:040x}"
            decoded = {p["name"]: p["value"] for p in event["decoded"]["parameters"]}
            require(
                decoded["reserve"].lower() == asset
                and decoded["onBehalfOf"].lower() == beneficiary
                and decoded["user"].lower() == user
                and int(decoded["amount"]) == data[1],
                "Manual lending decode differs from explorer ABI decode",
            )
            if beneficiary != MAIN:
                continue
            require(
                tx in focus_hashes,
                "Lending event outside complete main-account selection",
            )
            row = {
                "tx": tx,
                "block": meta["block_number"],
                "timestamp": stamp(meta["timestamp"]),
                "timestamp_utc": meta["timestamp"],
                "asset": ASSETS[asset],
                "asset_address": asset,
                "amount_raw": data[1],
                "amount": str(Decimal(data[1]) / UNIT),
                "beneficiary": beneficiary,
            }
            if not is_borrow:
                deposits.append(row)
                continue
            input_words = words("0x" + meta["raw_input"][10:])
            require(
                meta["raw_input"][:10] == "0xa415bcad"
                and input_words[0] == int(asset, 16)
                and input_words[1] == data[1]
                and input_words[4] == int(MAIN, 16),
                "Borrow calldata mismatch",
            )
            paid = sum(
                words(y["data"])[0]
                for y in logs(tx)
                if y["address"]["hash"].lower() == asset
                and y["topics"][0] == TRANSFER
                and address(y["topics"][2]) == MAIN
            )
            require(
                paid == data[1],
                "Borrow amount differs from token payment to main account",
            )
            trace = raw("trace-" + tx)
            require(
                trace["from"].lower() == MAIN
                and trace["to"].lower() == LENDING
                and trace["input"].lower() == meta["raw_input"].lower()
                and not trace.get("error"),
                "Trace root mismatch",
            )
            flattened = list(calls(trace))
            prices = [
                x
                for x in flattened
                if x.get("to", "").lower() == ORACLE
                and x.get("from", "").lower() == LENDING
                and x.get("input", "").lower() == "0xb3596f07" + "0" * 24 + MOO[2:]
            ]
            require(
                bool(prices)
                and all(len(words(x["output"])) == 1 for x in prices)
                and len({x["output"] for x in prices}) == 1,
                "Ambiguous actual MOO collateral quote",
            )
            oracle_raw = words(prices[0]["output"])[0]
            feed_calls = [
                x
                for x in flattened
                if x.get("from", "").lower() == FEED
                and x.get("input", "").startswith("0x8c86f1e4")
            ]
            require(
                len(feed_calls) == len(prices)
                and all(
                    words("0x" + x["input"][10:])
                    == [int(MOO, 16), 10**18, int(MCELO, 16)]
                    and words(x["output"]) == [oracle_raw]
                    for x in feed_calls
                ),
                "MOO/mCELO feed mapping mismatch",
            )
            row.update(
                {
                    "MOO_oracle_quote_raw": oracle_raw,
                    "MOO_oracle_quote": str(Decimal(oracle_raw) / UNIT),
                    "quote_unit": "CELO account-value unit per MOO; source quote is mCELO/MOO",
                    "underlying_token_paid_raw": paid,
                    "MOO_oracle_call_count": len(prices),
                }
            )
            borrows.append(row)
    for rows in (account_rows, borrows, deposits):
        rows.sort(key=lambda x: (x["timestamp"], x["tx"]))
    expected_borrows = {
        x["tx"] for x in account_rows if x["status"] == "ok" and x["method"] == "borrow"
    }
    require(
        {x["tx"] for x in borrows} == expected_borrows,
        "Missing main-account borrow event or trace",
    )
    expected_deposits = {
        x["tx"]
        for x in account_rows
        if x["status"] == "ok" and x["method"] == "deposit"
    }
    require(
        {x["tx"] for x in deposits} == expected_deposits, "Missing main-account deposit"
    )
    last_buy = main_swaps[-1]
    after = [x for x in borrows if x["timestamp"] > last_buy["timestamp"]]
    by_asset = lambda rows: {
        symbol: str(sum(Decimal(x["amount"]) for x in rows if x["asset"] == symbol))
        for symbol in sorted({x["asset"] for x in rows})
    }
    other = [
        x
        for x in swaps
        if main_swaps[0]["timestamp"] <= x["timestamp"] <= borrows[-1]["timestamp"]
        and not x["main_account_transaction"]
    ]
    result = {
        "pool_log_partitions": leaves,
        "pool_events": len(events),
        "pool_swaps": len(swaps),
        "pool_sync_events": len(reserve_states),
        "main_account_history_outgoing": len(outgoing),
        "main_account_focus_transactions": len(focus),
        "main_account_method_counts": dict(
            collections.Counter((x["method"] + ":" + x["status"]) for x in account_rows)
        ),
        "main_pool_buys": len(main_swaps),
        "main_borrow_events": len(borrows),
        "main_deposit_events": len(deposits),
        "main_mCELO_net_input": str(
            sum(Decimal(x["net_mCELO_to_pool"]) for x in main_swaps)
        ),
        "main_MOO_pool_output": str(
            -sum(Decimal(x["net_MOO_to_pool"]) for x in main_swaps)
        ),
        "deposit_totals": by_asset(deposits),
        "borrow_totals": by_asset(borrows),
        "oracle_initial": borrows[0]["MOO_oracle_quote"],
        "oracle_final": borrows[-1]["MOO_oracle_quote"],
        "oracle_final_to_initial": str(
            Decimal(borrows[-1]["MOO_oracle_quote"])
            / Decimal(borrows[0]["MOO_oracle_quote"])
        ),
        "pool_initial": main_swaps[0]["pre_mCELO_per_MOO"],
        "pool_after_last_main_buy": last_buy["post_mCELO_per_MOO"],
        "pool_change_factor": str(
            Decimal(last_buy["post_mCELO_per_MOO"])
            / Decimal(main_swaps[0]["pre_mCELO_per_MOO"])
        ),
        "last_main_buy_utc": last_buy["timestamp_utc"],
        "last_borrow_utc": borrows[-1]["timestamp_utc"],
        "borrows_after_last_main_buy": len(after),
        "borrow_totals_after_last_main_buy": by_asset(after),
        "other_pool_swaps_in_focus": len(other),
        "other_swap_direction_counts": dict(
            collections.Counter(x["direction"] for x in other)
        ),
        "other_net_mCELO_to_pool": str(
            sum(Decimal(x["net_mCELO_to_pool"]) for x in other)
        ),
        "first_buy_to_next_pre_price_decline_pct": str(
            (
                Decimal(main_swaps[1]["pre_mCELO_per_MOO"])
                / Decimal(main_swaps[0]["post_mCELO_per_MOO"])
                - 1
            )
            * 100
        ),
        "limitations": [
            "One historical event, selected after public reporting; no prevalence or population inference.",
            "Historical logs and traces come from one indexer, not independent archive-node replay or receipt inclusion proofs.",
            "Other transaction origins are not proof of independent beneficial owners or arbitrage motives.",
            "Pool net mCELO inputs include accrued interest/dust and are not a wallet cash-cost estimate.",
            "Borrow totals are gross token credits, not profit, net loss, or a dollar-denominated damage estimate.",
            "The underlying sliding-window contract is not source-verified in the captured explorer response; no exact averaging horizon is claimed.",
            "Oracle quotes are observed at borrowing transactions, not a continuous historical oracle time series.",
        ],
    }
    write_csv("pool-swaps.csv", swaps)
    write_csv("pool-reserve-states.csv", reserve_states)
    write_csv("main-account-transactions.csv", account_rows)
    write_csv("main-pool-buys.csv", main_swaps)
    write_csv("main-borrows.csv", borrows)
    write_csv("main-deposits.csv", deposits)
    (OUT / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
