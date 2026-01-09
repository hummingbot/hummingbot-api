## How we work together (short)

- When starting a multi-step change I will create a short todo list (visible in the session) and mark items as in-progress/completed.
- For actionable changes I will modify repository files and run quick checks (tests, tiny smoke runs) as appropriate.
- If you want long-lived definitions or preferences (naming, abbreviations, default slippage, preferred bands), add them to `LEXICON.md` or `CONTEXT.md` and I will read them before edits.
- Useful files I typically check early: `AGENTS.md`, `README.md`, `package.json`/`pyproject.toml`, `docker-compose.yml`, `gateway-src/` docs and `src/` entry points.

### Suggestions for you

- Add a short `SESSION_NOTES.md` entry when you step away or change intent; I'll pick it up in the next action.
- If you want the assistant to always prefer a setting (for example slippagePct=1.0 or default dev RPC), put it in `CONTEXT.md` under a clear `Defaults` section.

### Assistant startup command

Add this small helper command to run locally and show the assistant files quickly:

```bash
./.assistant/load_assistant_context.sh
```

If you want me to consult `.assistant/` automatically at the start of each session, explicitly tell me to do so at the beginning of the session (for example: "Consult `.assistant/` first"). I will follow that convention while working in this workspace.

### Pre-quote checklist (new convention)

Before requesting any `quote-position` or attempting to build transaction calldata, verify the wallet and token resources to avoid failed transactions and wasted gas. Follow these steps:

1. Check native token balance (BNB on BSC) to ensure there is enough for gas.
2. Check token balances for the wallet (base/quote tokens involved) to ensure sufficient amounts.
3. Check token allowances for the Position Manager (spender: `pancakeswap/clmm` via the allowances route) so the mint will not fail due to insufficient allowance.
4. Only request `quote-position` once the above checks pass; otherwise surface a clear error and suggest which step needs action (approve, transfer funds, or choose a smaller amount).

Minimum gas reserve

- Always keep at least 0.002 BNB (or equivalent WBNB) available in the wallet as a gas reserve. The assistant will check the sum of native BNB + WBNB and warn if it's below this threshold before proceeding with a quote or an open.

Example Gateway calls (replace wallet and tokens). Note: the codebase and Gateway now use a canonical "chain-network" identifier
in many places (for example `bsc-mainnet`, `ethereum-mainnet`, `solana-mainnet-beta`). Where older curl examples pass
separate `chain` and `network` values, prefer using the combined `chain-network` format in scripts and models.

Canonical examples (preferred):

```bash
# 1) Check balances (POST) using the chain/network pair split out from a canonical id.
#    Here we show the equivalent of 'bsc-mainnet' -> chain='bsc', network='mainnet'.
curl -X POST http://localhost:15888/chains/bsc/balances \
  -H "Content-Type: application/json" \
  -d '{"network":"mainnet","address":"<WALLET_ADDRESS>","tokens":["<BASE_TOKEN_ADDRESS>","<QUOTE_TOKEN_ADDRESS>"]}'

# 2) Check allowances (POST)
curl -X POST http://localhost:15888/chains/bsc/allowances \
  -H "Content-Type: application/json" \
  -d '{"network":"mainnet","address":"<WALLET_ADDRESS>","spender":"pancakeswap/clmm","tokens":["<BASE_TOKEN_ADDRESS>"]}'

# 3) When checks pass, call quote-position. Many higher-level scripts accept a single "chain-network"
#    CLI argument (for example: --chain-network bsc-mainnet) which they split into chain='bsc' and network='mainnet'.
curl -sG "http://localhost:15888/connectors/pancakeswap/clmm/quote-position" \
  --data-urlencode "network=mainnet" \
  --data-urlencode "poolAddress=<POOL_ADDRESS>" \
  --data-urlencode "baseTokenAmount=200" \
  --data-urlencode "lowerPrice=<LOWER>" \
  --data-urlencode "upperPrice=<UPPER>"
```

Legacy example (older docs): some quick examples in the repo used `chains/ethereum` plus `network=bsc` which is
confusing; ignore those—the correct interpretation is to map a canonical id like `bsc-mainnet` to `chains/bsc` and
`network=mainnet` when forming low-level Gateway calls.

If you'd like this enforced automatically, tell me to "Auto-check balances before quote" at session start and I'll perform these checks before any quote/open requests in the session.

