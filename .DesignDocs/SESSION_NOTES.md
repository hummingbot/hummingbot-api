# Session notes (latest)

Date: 2026-01-08

- Wallet in use: 0xA57d70a25847A7457ED75E4e04F8d00bf1BE33bC
- BIA token: 0x924fa68a0FC644485b8df8AbfA0A41C2e7744444 (decimals 18)
- WBNB: 0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c
- Pancake V3 pool of interest: 0xc397874a6Cf0211537a488fa144103A009A6C619

Recent actions:

- Open attempt (baseMode) with baseTokenAmount=200 and ±3% bounds returned HTTP 500 from Gateway.
- Successful open using `quoteTokenAmount` for an unrelated pool created position `6243429` (closed later).
- Closed position `6243429` — tx signature: 0x58e8d913bd21c9a6051bae944868f77acc0f31c83058af7168b3b74d4f104ec6

Notes for next session:

- Start by consulting `CONTEXT.md`, `LEXICON.md` and `SESSION_NOTES.md`.
- If retrying a deposit (base) open for BIA, enable Gateway stdout logging to capture any stack traces if it fails.
