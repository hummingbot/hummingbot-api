## Lexicon (initial)

- `AGENTS.md` — repository guidance for AI/code agents; I read this to follow repo conventions.
- `BIA` — token symbol used in our session. Address: 0x924fa68a0FC644485b8df8AbfA0A41C2e7744444 (decimals: 18).
- `WBNB` — Wrapped BNB token used as pair quote for Pancake V3.
- `poolAddress` — Pancake V3 pool contract address (example: 0xc397874a6Cf0211537a488fa144103A009A6C619).
- `baseTokenAmount` — when opening a position in deposit (base) mode, the number of base tokens to deposit (human units).
- `quoteTokenAmount` — alternative open mode; the quote token amount to provide.
- `tick` / `binId` — V3 CLMM discrete price coordinate; the code sometimes calls these `tick` or `binId`.
- `liquidity` — SDK-exposed liquidity metric for a position.
- `positionAddress` / `positionId` — the NFT token ID returned by the PositionManager when minting a CLMM position.
- `ZERO_LIQUIDITY` — a pre-onchain invariant error from the SDK when calculated liquidity is zero for the requested params.
- `CALL_EXCEPTION` — on-chain revert/error returned by EVM providers when a transaction reverts.

- `open position` — shorthand meaning "create a CLMM position and deposit the specified base/quote tokens" (i.e., mint the position NFT and transfer liquidity into it). Use this phrase when you want the assistant to perform both the creation and the deposit step.

If you want, add more project-specific terms here and I will use them consistently in code changes and reports.
