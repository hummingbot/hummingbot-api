## Purpose

This small workspace folder is maintained by the assistant to store short, persistent context files that help our collaboration. Treat these files as editable notes for the pair-programming session. Keep them lightweight—small, clear, and actionable.

## How I use this file

- I will summarize high-level repository context, conventions, and any assumptions we make for tasks.
- I will add brief pointers when we change important configuration (RPC, passphrases, wallet locations).
- Update process: the assistant will edit this file when major context changes; you can edit it manually too.

## Important note

I do not have persistent memory across separate chat sessions. Storing context in the repo (under `.assistant/`) ensures the information remains available in the codebase and can be re-read on subsequent runs or by other developers.

## Tooling note

- Prefer using the plain `curl` command for fetching RPC/API JSON in our workflows and scripts (avoid relying on a system-installed `jq`). If you need to parse or decode JSON, use a small Python snippet or the repository's helper scripts so we don't introduce an external dependency on `jq` in automation steps.

## Relevant doc pointers (already read)

- `AGENTS.md` — contains project-level agent instructions and build/run/test tips.
- `README.md` — repo overview and quick start.
- `gateway-src/AGENTS.md` (this file) — used to guide gateway interactions and conventions.

## Assistant convention

Add this line to make the intent explicit for future collaborators and assistants:

"On session start, please consult the `.assistant/` directory first for context and lexicon files (e.g., `CONTEXT.md`, `LEXICON.md`, `USAGE.md`, `SESSION_NOTES.md`). Run `./.assistant/load_assistant_context.sh` to print them locally. If you are an automated agent, read these files before making changes and respect their defaults."

## Defaults (assistant behavior)

- Default token type: BNB (native token / wrapped WBNB when needed). Unless you explicitly tell me otherwise, assume any CLMM or BSC-related position or token you name refers to a BNB token.
- Default network id for BSC operations: `ethereum-bsc`. Use this identifier when calling MCP/Gateway endpoints that require a network id (for example: CLMM pool management, positions, and quotes).
- Default ownership assumption: when you explicitly state that "we own" a position or token, I will assume the wallet currently loaded into the Gateway is the correct owner and proceed without asking for additional ownership confirmation.

### CLMM open/deposit convention

- When requesting a deposit ratio or opening a CLMM position, always provide both the baseTokenAmount and the quoteTokenAmount when possible. The Gateway's on-chain mint calculations (tick/bin rounding and liquidity math) can produce ZERO_LIQUIDITY if only one side is provided; supplying both sides (or using the `quote-position` endpoint first and then passing both amounts to `open-position`) prevents that class of failures.
- From now on, the assistant will include both amounts in its `open position` calls whenever a quote is available or when you instruct it to open a position.

## Pre-quote convention

As a repository convention, before requesting a `quote-position` the assistant (or human operator) should verify wallet resources and allowances. This minimizes failed transactions and wasted gas. The minimal pre-quote checks are:

1. Native token balance (BNB on BSC) — enough for gas.
2. Token balances — ensure the wallet has the requested base/quote amount.
3. Token allowance — Position Manager (spender resolved via `pancakeswap/clmm`) must have sufficient allowance for the tokens being used.

See `.assistant/USAGE.md` for CLI examples that call the Gateway endpoints for balances and allowances. If you'd like the assistant to perform these checks automatically during a session, start the conversation with: "Auto-check balances before quote" and I will execute the checks before any quote/open requests.

If you'd like me to persist additional state, we can add files like `SESSION_NOTES.md`, `NOTES.md`, or a small JSON index to track recent actions.

## Automation-first workflow (team policy)

- Goal: All CLMM stake/unstake (withdraw) flows should be automatable without human interaction in the hot path. Manual, interactive steps (for example, using the BscScan "Write Contract" UI) are considered a fallback only.
- Operator model: In our deployment the operator (you) is the single trusted human who authorizes the Gateway to sign transactions. The assistant will assume Gateway-signed mode (gateway_sign=true) by default for scheduled/automated withdraws unless explicitly overridden.
- Security rules we follow in repo automation:
	1. Only load keystores for operator accounts that you explicitly add to the Gateway (do NOT auto-import arbitrary private keys).
 2. Require a one-time human confirmation when adding a new signer keystore to the Gateway in production; this can be enforced by CI/ops policies outside this repo.
 3. Log every gateway-signed withdraw with: requester id, tokenId, target `to` address, timestamp, and txHash. Record logs to DB events and persistent logs for audit.
 4. Implement idempotency and per-token locks to prevent double-withdraws.
 5. Provide both modes in the API: `prepare-withdraw` (returns calldata) and `execute-withdraw` (gateway_sign boolean). Automation should prefer `execute-withdraw` when Gateway has an authorized signer.

If you'd like stricter controls (HSM-only signing, multi-sig approval, or time-lock windows) we can add those later; for now this file documents the default automation-first policy for this repository.

## Gateway token / network convention

- When registering tokens or pools with the Gateway via the MCP (the `manage_gateway_config` endpoints), always use the canonical network id the Gateway expects. For BSC that id is `ethereum-bsc` (not `bsc` or `bsc-mainnet`).
- The MCP endpoints perform strict validation: include explicit `base_address` and `quote_address` when adding pools, and prefer the `pool_base`/`pool_quote` friendly names alongside the addresses. If you see 422 validation errors, check for missing keys or the wrong network id.
- After adding tokens or pools via MCP, restart the Gateway process so the running Gateway loads the new configuration (MCP will usually indicate "Restart Gateway for changes to take effect"). If MCP cannot manage the container, restart it manually (docker-compose, systemd, etc.).
