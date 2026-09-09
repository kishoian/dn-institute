"""Reconcile published ledgers against raw logs, calldata and trace returns.

Does not import the producer. Uses exact rational arithmetic for pool prices,
and derives loan amounts from calldata, separately from event-word decoding.
"""

import csv
import gzip
import hashlib
import json
import zipfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(name):
    with zipfile.ZipFile(ROOT / "sources.zip") as archive:
        return json.loads(gzip.decompress(archive.read("raw/" + name + ".json.gz")))[
            "response"
        ]


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    require(
        hashlib.sha256((ROOT / manifest["file"]).read_bytes()).hexdigest()
        == manifest["sha256"],
        "Archive hash mismatch",
    )
    with zipfile.ZipFile(ROOT / manifest["file"]) as archive:
        for item in manifest["files"]:
            require(
                hashlib.sha256(archive.read(item["file"])).hexdigest()
                == item["sha256"],
                "Source hash mismatch",
            )
    s = json.loads((ROOT / "analysis/summary.json").read_text())
    events = []
    for part in s["pool_log_partitions"]:
        events += read(f"pool-{part['start']}-{part['end']}")["result"]
    by_id = {(x["transactionHash"], int(x["logIndex"], 16)): x for x in events}
    require(len(by_id) == len(events) == s["pool_events"], "Pool event count mismatch")
    states = list(csv.DictReader((ROOT / "analysis/pool-reserve-states.csv").open()))
    require(len(states) == s["pool_sync_events"], "Reserve state count mismatch")
    for state in states:
        event = by_id[state["tx"], int(state["log_index"])]
        require(
            event["topics"][0]
            == "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1",
            "Reserve state does not correspond to Sync",
        )
        payload = bytes.fromhex(event["data"][2:])
        moo = int.from_bytes(payload[:32], "big")
        mcelo = int.from_bytes(payload[32:], "big")
        require(
            moo == int(state["MOO_raw"]) and mcelo == int(state["mCELO_raw"]),
            "Sync reserve mismatch",
        )
        exact = Fraction(mcelo, moo)
        require(
            abs(exact - Fraction(state["mCELO_per_MOO"]))
            <= abs(exact) * Fraction(1, 10**58),
            "Sync price mismatch",
        )
    rows = list(csv.DictReader((ROOT / "analysis/pool-swaps.csv").open()))
    for row in rows:
        event = by_id[row["tx"], int(row["log_index"])]
        sync = by_id[row["tx"], int(row["log_index"]) - 1]
        swap = bytes.fromhex(event["data"][2:])
        reserves = bytes.fromhex(sync["data"][2:])
        require(len(swap) == 128 and len(reserves) == 64, "Unexpected event sizes")
        amounts = [int.from_bytes(swap[i : i + 32], "big") for i in range(0, 128, 32)]
        post = [int.from_bytes(reserves[i : i + 32], "big") for i in (0, 32)]
        require(
            amounts == [int(row[k]) for k in ["a0in", "a1in", "a0out", "a1out"]],
            "Swap amount mismatch",
        )
        prior = [post[i] + amounts[i + 2] - amounts[i] for i in (0, 1)]
        require(
            prior == [int(row["pre0"]), int(row["pre1"])]
            and post == [int(row["post0"]), int(row["post1"])],
            "Reserve reconstruction mismatch",
        )
        for key, pair in [("pre_mCELO_per_MOO", prior), ("post_mCELO_per_MOO", post)]:
            exact = Fraction(pair[1], pair[0])
            rounded = Fraction(row[key])
            require(
                abs(exact - rounded) <= abs(exact) * Fraction(1, 10**58),
                "Price ratio outside rounding tolerance",
            )
    loans = list(csv.DictReader((ROOT / "analysis/main-borrows.csv").open()))
    totals = {}
    for row in loans:
        meta = read("tx-" + row["tx"])
        calldata = bytes.fromhex(meta["raw_input"][10:])
        amount = int.from_bytes(calldata[32:64], "big")
        require(amount == int(row["amount_raw"]), "Loan calldata amount mismatch")
        require(
            Fraction(row["amount"]) == Fraction(amount, 10**18),
            "Loan displayed unit mismatch",
        )
        trace = read("trace-" + row["tx"])
        stack = [trace]
        returns = []
        while stack:
            call = stack.pop()
            stack.extend(call.get("calls", []))
            if (
                call.get("to", "").lower()
                == "0xba2224905ad3cdba6c1b764cd62fda52bd524d29"
                and call.get("input", "").lower()
                == "0xb3596f07" + "0" * 24 + "17700282592d6917f6a73d0bf8accf4d578c131e"
            ):
                returns.append(int(call["output"], 16))
        require(
            bool(returns)
            and all(x == int(row["MOO_oracle_quote_raw"]) for x in returns),
            "Oracle trace mismatch",
        )
        require(
            Fraction(row["MOO_oracle_quote"]) == Fraction(returns[0], 10**18),
            "Oracle unit mismatch",
        )
        totals[row["asset"]] = totals.get(row["asset"], Fraction(0)) + Fraction(
            amount, 10**18
        )
    require(
        totals == {k: Fraction(v) for k, v in s["borrow_totals"].items()},
        "Loan totals mismatch",
    )
    buys = [x for x in rows if x["main_account_transaction"] == "True"]
    require(len(buys) == s["main_pool_buys"], "Main buy count mismatch")
    require(
        sum(Fraction(x["net_mCELO_to_pool"]) for x in buys)
        == Fraction(s["main_mCELO_net_input"]),
        "Main mCELO pool flow mismatch",
    )
    after = [x for x in loans if int(x["timestamp"]) > int(buys[-1]["timestamp"])]
    after_totals = {
        asset: sum(Fraction(x["amount"]) for x in after if x["asset"] == asset)
        for asset in {x["asset"] for x in after}
    }
    require(
        after_totals
        == {k: Fraction(v) for k, v in s["borrow_totals_after_last_main_buy"].items()},
        "Post-purchase loan totals mismatch",
    )
    factor = Fraction(loans[-1]["MOO_oracle_quote_raw"]) / Fraction(
        loans[0]["MOO_oracle_quote_raw"]
    )
    require(
        abs(factor - Fraction(s["oracle_final_to_initial"])) < Fraction(1, 10**55),
        "Oracle increase mismatch",
    )
    result = {
        "source_files_checked": len(manifest["files"]),
        "pool_events_checked": len(events),
        "pool_swaps_checked": len(rows),
        "sync_states_checked": len(states),
        "loan_calldata_and_oracle_returns_checked": len(loans),
        "main_buys_checked": len(buys),
        "post_last_purchase_loans_checked": len(after),
        "headline_reconciliation": "pass",
        "limitation": "Independent implementation on the same saved indexer evidence, not independent provider corroboration.",
    }
    (ROOT / "analysis/independent-audit.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
