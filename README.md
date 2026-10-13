# SOLANA_LAUNCH_LAB

An independent Solana token discovery and paper-trading research project.

## Features
- Discovers Solana token profiles through Dexscreener public endpoints.
- Retrieves pair price, liquidity, volume, transaction and price-change data.
- Applies configurable market-activity filters.
- Simulates entries, take-profit, stop-loss and maximum holding time.
- Writes candidate observations to candidates.csv.
- Records completed simulated trades in paper_trades.csv.
- Saves virtual account state in state.json.

## Run
    python main.py

Edit config.json to adjust filters and simulated risk settings.

## Important limitations
This is a research simulator, not a prediction engine. The +200% take-profit
setting is a test hypothesis, not a forecast. Newly discovered pools may be
older than the token itself. Public market data can be incomplete or delayed.
The simulator does not accurately model slippage, fees, failed fills, MEV,
rug pulls, liquidity removal or real execution. It does not connect to a wallet
and does not place live trades.

The simulator's price-based exits are approximate: if a pair disappears or
data is unavailable, a position may remain open until a usable price returns.
Use virtual funds only.
