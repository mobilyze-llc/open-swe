# Mobilyze extension guidance

This directory contains the complete Mobilyze production delta for Open SWE. Read `docs/mobilyze/FORK_ARCHITECTURE.md` before editing it.

- Code here must implement only approved agent execution, Apple sandbox/profile routing, or decorrelated review. Approved executor kinds are `api_model`, `cli_agent`, and `external_helper` as defined in `docs/mobilyze/FORK_ARCHITECTURE.md`.
- For `api_model`, Open SWE owns conversation state, model turns, tools, checkpoints, cancellation, and task results. Mobilyze selects an approved Agent Definition and endpoint profile; it does not add a second agent loop.
- For `cli_agent`, the provider CLI owns its internal loop while the Mobilyze harness owns outer lifecycle, isolation, normalized status/artifacts, and exact provider-session continuity.
- For `external_helper`, require a pinned/hash-verified executable and one bounded explicit input/output contract.
- Keep each module single-purpose and below 350 lines when introduced. Split before a module reaches 600 lines.
- Use explicit protocols, dataclasses, and provider-specific adapters. Do not build a general plugin framework.
- Runtime code is async-only. Own subprocess groups, cancellation, timeouts, and cleanup explicitly.
- Provider-specific argv, event parsing, and errors stay in that provider's adapter. Shared runtime code must remain provider-neutral.
- Prompts should be delivered through stdin or protected files when supported, not exposed in process arguments by default.
- Capture and persist provider-generated session IDs. Resume exact sessions; do not reconstruct continuity by concatenating transcripts.
- Never retry a side-effectful run unless the prior attempt is proven not to have started or the same provider session is being resumed.
- Agent Definitions own executor kind, behavior, model policy, instructions, schemas, limits, and named endpoint/profile/tool/helper references. Executors must not be introduced implicitly or bypass those identities.
- Endpoint profiles describe deployment metadata and capabilities, not separate agent implementations. Bind their identity and capability hash in evidence and compatibility tests without changing graph selection.
- Task profiles own the sandbox/image, skills, MCP servers, permissions, credentials, provider homes, and cleanup. API agents receive MCP capabilities through Open SWE's MCP bridge, not Codex-native configuration.
- Direct or unclassified model calls remain prohibited. Approved official or subscription-backed API access uses `api_model`; do not create separate executor kinds for compatible endpoints.
- Tests belong under `tests/mobilyze/` and must cover public contracts, parser fixtures, cancellation, and cleanup behavior.
- Changes to upstream-owned modules may only register or select a component from this directory and must be declared in `config/mobilyze/architecture-guardrails.json`.
