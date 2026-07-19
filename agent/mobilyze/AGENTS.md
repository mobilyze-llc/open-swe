# Mobilyze extension guidance

This directory contains the complete Mobilyze production delta for Open SWE. Read `docs/mobilyze/FORK_ARCHITECTURE.md` before editing it.

- Code here must implement only native CLI harness execution, Apple sandbox/profile routing, or decorrelated review.
- Keep each module single-purpose and below 350 lines when introduced. Split before a module reaches 600 lines.
- Use explicit protocols, dataclasses, and provider-specific adapters. Do not build a general plugin framework.
- Runtime code is async-only. Own subprocess groups, cancellation, timeouts, and cleanup explicitly.
- Provider-specific argv, event parsing, and errors stay in that provider's adapter. Shared runtime code must remain provider-neutral.
- Prompts should be delivered through stdin or protected files when supported, not exposed in process arguments by default.
- Capture and persist provider-generated session IDs. Resume exact sessions; do not reconstruct continuity by concatenating transcripts.
- Never retry a side-effectful run unless the prior attempt is proven not to have started or the same provider session is being resumed.
- Stage skills, MCP configuration, credentials, and provider homes per task inside the selected sandbox profile.
- Tests belong under `tests/mobilyze/` and must cover public contracts, parser fixtures, cancellation, and cleanup behavior.
- Changes to upstream-owned modules may only register or select a component from this directory and must be declared in `config/mobilyze/architecture-guardrails.json`.
