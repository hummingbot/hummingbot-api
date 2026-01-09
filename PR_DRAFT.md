PR Title: CLMM Stake, Refactor, and Project Hygiene Improvements

Summary
-------
This PR introduces the CLMM stake endpoint, refactors and organizes scripts, and enforces project hygiene for maintainability and clarity.

Key Changes
-----------
- Added POST /gateway/clmm/stake endpoint and supporting models, client methods, and tests.
- Migrated and renamed CLMM-related scripts for semantic clarity (CLMM prefixing, demo scripts moved to `scripts/demos`, utility scripts to `scripts`).
- Organized design docs into `.DesignDocs`.
- Reverted unnecessary or trivial changes in scripts; only meaningful code modifications remain.
- Added concise test guidelines and scaffolding for consistent testing.

Rationale
---------
- Completes the CLMM lifecycle by enabling position staking and event recording.
- Improves codebase clarity and maintainability by enforcing semantic naming and directory structure.
- Ensures only essential changes are present, reducing review overhead and future merge conflicts.
- Provides a foundation for reliable CI and easier onboarding for contributors.

Testing & Validation
--------------------
- All new and refactored scripts tested locally and in Docker test-stage image.
- Unit tests for CLMM stake endpoint cover both success and edge cases.
- Test guidelines and scaffolding validated with new and existing tests.

Next Steps
----------
- Replicate all applicable gateway files into the feature branch: https://github.com/VeXHarbinger/hummingbot-gateway/tree/feature/clmm-add-remove-liquidity
- After confirming all changes are present, delete the temporary CLMM-LP-Stake-Network branch.

Reviewer Checklist
------------------
- [ ] CLMM stake endpoint and models are correct
- [ ] Project structure and naming are clear and consistent
- [ ] Only meaningful code changes are present
- [ ] Tests and guidelines are sufficient
- [ ] Ready for feature branch replication and cleanup

Notes
-----

