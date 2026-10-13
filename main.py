import csv
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE = "https://api.dexscreener.com"
CONFIG_FILE = "config.json"
STATE_FILE = "state.json"
CANDIDATES_FILE = "candidates.csv"
TRADES_FILE = "paper_trades.csv"

CANDIDATE_FIELDS = [
    "time_utc", "token_name", "symbol", "token_address",
    "pair_address", "pair_url", "age_seconds", "price_usd",
    "liquidity_usd", "volume_5m_usd", "buys_5m", "sells_5m",
    "transactions_5m", "buy_ratio", "price_change_5m",
    "qualified"
]

TRADE_FIELDS = [
    "opened_utc", "closed_utc", "symbol", "token_address",
    "pair_address", "entry_price", "exit_price", "stake_usd",
    "pnl_usd", "return_percent", "reason", "balance_after"
]


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, data):
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp, path)


def request_json(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SOLANA-LAUNCH-LAB/1.0"}
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def ensure_csv(path, fields):
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fields).writeheader()


def append_csv(path, fields, row):
    ensure_csv(path, fields)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writerow(row)


def get_solana_profiles():
    urls = [
        BASE + "/token-profiles/latest/v1",
        BASE + "/token-boosts/latest/v1"
    ]
    profiles = {}
    for url in urls:
        try:
            data = request_json(url)
            if not isinstance(data, list):
                continue
            for item in data:
                if item.get("chainId") != "solana":
                    continue
                address = item.get("tokenAddress")
                if address:
                    profiles[address] = item
        except Exception as exc:
            print(f"[WARN] Discovery request failed: {exc}")
    return list(profiles.values())


def get_pairs(addresses, batch_size):
    pairs = []
    for start in range(0, len(addresses), batch_size):
        batch = addresses[start:start + batch_size]
        if not batch:
            continue
        url = BASE + "/tokens/v1/solana/" + ",".join(batch)
        try:
            data = request_json(url)
            if isinstance(data, list):
                pairs.extend(data)
        except Exception as exc:
            print(f"[WARN] Pair request failed: {exc}")
        time.sleep(0.25)
    return pairs


def number(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def get_metrics(pair, token_address):
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}

    if base.get("address", "").lower() == token_address.lower():
        token = base
    elif quote.get("address", "").lower() == token_address.lower():
        token = quote
    else:
        token = base

    created = number(pair.get("pairCreatedAt"))
    age = max(0.0, time.time() - created / 1000) if created else 999999

    txns = (pair.get("txns") or {}).get("m5") or {}
    buys = number(txns.get("buys"))
    sells = number(txns.get("sells"))
    total = buys + sells
    buy_ratio = buys / total if total else 0.0

    liquidity = number((pair.get("liquidity") or {}).get("usd"))
    volume = number((pair.get("volume") or {}).get("m5"))
    change = number((pair.get("priceChange") or {}).get("m5"))
    price = number(pair.get("priceUsd"))

    return {
        "token_name": token.get("name", "Unknown"),
        "symbol": token.get("symbol", "UNKNOWN"),
        "token_address": token_address,
        "pair_address": pair.get("pairAddress", ""),
        "pair_url": pair.get("url", ""),
        "age_seconds": round(age, 1),
        "price_usd": price,
        "liquidity_usd": liquidity,
        "volume_5m_usd": volume,
        "buys_5m": int(buys),
        "sells_5m": int(sells),
        "transactions_5m": int(total),
        "buy_ratio": round(buy_ratio, 3),
        "price_change_5m": change
    }


def qualifies(m, cfg):
    return all([
        0 < m["price_usd"],
        m["age_seconds"] <= cfg["max_discovery_age_seconds"],
        m["liquidity_usd"] >= cfg["min_liquidity_usd"],
        m["volume_5m_usd"] >= cfg["min_volume_5m_usd"],
        m["transactions_5m"] >= cfg["min_transactions_5m"],
        m["buys_5m"] >= cfg["min_buys_5m"],
        m["buy_ratio"] >= cfg["min_buy_ratio"],
        m["price_change_5m"] >= cfg["min_price_change_5m_percent"],
        m["price_change_5m"] <= cfg["max_price_change_5m_percent"]
    ])


def initial_state(cfg):
    return {
        "balance": cfg["starting_balance"],
        "positions": [],
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "seen_pairs": []
    }


def close_position(state, position, exit_price, reason):
    if exit_price <= 0 or position["entry_price"] <= 0:
        return

    stake = position["stake_usd"]
    ret = exit_price / position["entry_price"] - 1
    pnl = stake * ret
    state["balance"] += stake + pnl
    state["closed_trades"] += 1

    if pnl >= 0:
        state["wins"] += 1
    else:
        state["losses"] += 1

    append_csv(TRADES_FILE, TRADE_FIELDS, {
        "opened_utc": position["opened_utc"],
        "closed_utc": now_utc(),
        "symbol": position["symbol"],
        "token_address": position["token_address"],
        "pair_address": position["pair_address"],
        "entry_price": position["entry_price"],
        "exit_price": exit_price,
        "stake_usd": round(stake, 4),
        "pnl_usd": round(pnl, 4),
        "return_percent": round(ret * 100, 2),
        "reason": reason,
        "balance_after": round(state["balance"], 4)
    })

    print(
        f"[CLOSED] {position['symbol']} | {reason} | "
        f"PnL ${pnl:.2f} | Balance ${state['balance']:.2f}"
    )


def update_positions(state, price_map, cfg):
    remaining = []
    now = time.time()

    for pos in state["positions"]:
        price = price_map.get(pos["pair_address"], 0)
        if price <= 0:
            remaining.append(pos)
            continue

        change = price / pos["entry_price"] - 1
        held_minutes = (now - pos["opened_epoch"]) / 60

        if change >= cfg["take_profit_percent"] / 100:
            close_position(state, pos, price, "TAKE_PROFIT")
        elif change <= -cfg["stop_loss_percent"] / 100:
            close_position(state, pos, price, "STOP_LOSS")
        elif held_minutes >= cfg["max_hold_minutes"]:
            close_position(state, pos, price, "MAX_HOLD")
        else:
            remaining.append(pos)

    state["positions"] = remaining


def main():
    cfg = load_json(CONFIG_FILE, {})
    required = [
        "scan_interval_seconds", "starting_balance", "paper_trade_size",
        "max_open_positions", "max_entry_age_seconds",
        "max_discovery_age_seconds", "take_profit_percent",
        "stop_loss_percent", "max_hold_minutes", "min_liquidity_usd",
        "min_volume_5m_usd", "min_transactions_5m", "min_buys_5m",
        "min_buy_ratio", "min_price_change_5m_percent",
        "max_price_change_5m_percent", "max_addresses_per_request"
    ]
    missing = [key for key in required if key not in cfg]
    if missing:
        raise ValueError("Missing config settings: " + ", ".join(missing))

    state = load_json(STATE_FILE, initial_state(cfg))
    ensure_csv(CANDIDATES_FILE, CANDIDATE_FIELDS)
    ensure_csv(TRADES_FILE, TRADE_FIELDS)

    print("=" * 58)
    print(" SOLANA LAUNCH LAB — PAPER RESEARCH TERMINAL")
    print(" Live market data | Simulated trades only")
    print("=" * 58)

    while True:
        started = time.time()
        try:
            profiles = get_solana_profiles()
            addresses = list(dict.fromkeys(
                p["tokenAddress"] for p in profiles if p.get("tokenAddress")
            ))
            pairs = get_pairs(
                addresses, cfg["max_addresses_per_request"]
            )

            # Keep the most liquid pair for each discovered token.
            best = {}
            for pair in pairs:
                base = pair.get("baseToken") or {}
                token_address = base.get("address", "")
                if not token_address:
                    continue
                old = best.get(token_address)
                new_liq = number((pair.get("liquidity") or {}).get("usd"))
                old_liq = number(
                    ((old or {}).get("liquidity") or {}).get("usd")
                )
                if old is None or new_liq > old_liq:
                    best[token_address] = pair

            metrics = []
            for address, pair in best.items():
                m = get_metrics(pair, address)
                m["qualified"] = qualifies(m, cfg)
                metrics.append(m)
                append_csv(CANDIDATES_FILE, CANDIDATE_FIELDS, {
                    "time_utc": now_utc(), **m,
                    "qualified": m["qualified"]
                })

            price_map = {
                m["pair_address"]: m["price_usd"]
                for m in metrics if m["pair_address"] and m["price_usd"] > 0
            }
            update_positions(state, price_map, cfg)

            open_pairs = {p["pair_address"] for p in state["positions"]}
            seen = set(state.get("seen_pairs", []))
            qualified = [
                m for m in metrics
                if m["qualified"]
                and m["age_seconds"] <= cfg["max_entry_age_seconds"]
                and m["pair_address"]
                and m["pair_address"] not in seen
                and m["pair_address"] not in open_pairs
            ]
            qualified.sort(
                key=lambda m: (
                    m["buy_ratio"],
                    m["volume_5m_usd"],
                    m["liquidity_usd"]
                ),
                reverse=True
            )

            for m in qualified:
                if len(state["positions"]) >= cfg["max_open_positions"]:
                    break

                stake = min(
                    cfg["paper_trade_size"],
                    max(0.0, state["balance"])
                )
                if stake <= 0:
                    print("[PAUSED] Virtual balance is insufficient.")
                    break

                state["balance"] -= stake
                pos = {
                    "opened_utc": now_utc(),
                    "opened_epoch": time.time(),
                    "symbol": m["symbol"],
                    "token_address": m["token_address"],
                    "pair_address": m["pair_address"],
                    "entry_price": m["price_usd"],
                    "stake_usd": stake
                }
                state["positions"].append(pos)
                seen.add(m["pair_address"])

                print(
                    f"[PAPER BUY] {m['symbol']} | "
                    f"Entry ${m['price_usd']:.10f} | "
                    f"Liquidity ${m['liquidity_usd']:,.0f} | "
                    f"Buy ratio {m['buy_ratio']:.0%}"
                )

            state["seen_pairs"] = list(seen)[-5000:]
            save_json(STATE_FILE, state)

            print(
                f"\n[{now_utc()}] Profiles: {len(profiles)} | "
                f"Pairs: {len(pairs)} | Qualified: {len(qualified)}"
            )
            print(
                f"Virtual cash: ${state['balance']:.2f} | "
                f"Open positions: {len(state['positions'])} | "
                f"Closed: {state['closed_trades']} | "
                f"W/L: {state['wins']}/{state['losses']}"
            )
            print("-" * 58)

        except KeyboardInterrupt:
            save_json(STATE_FILE, state)
            print("\nStopped. State saved.")
            break
        except Exception as exc:
            print(f"[ERROR] {type(exc).__name__}: {exc}")

        elapsed = time.time() - started
        time.sleep(max(1, cfg["scan_interval_seconds"] - elapsed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        print(f"Startup error: {type(exc).__name__}: {exc}")
