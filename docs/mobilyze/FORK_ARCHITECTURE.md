# Mobilyze Open SWE fork architecture

This repository is a thin fork of `langchain-ai/open-swe`. Upstream Open SWE owns the control plane: webhooks, authentication, threads, dashboard, sandbox lifecycle, GitHub and Linear integrations, findings storage, and review publication.

Mobilyze code exists only where product requirements cannot be met upstream without a narrow extension.

## Permitted extension areas

1. **Approved agent execution** — run API models through Open SWE, provider-owned CLI agents through the Mobilyze harness, or pinned external helpers under the explicit contracts below.
2. **Apple execution** — route iOS and macOS work to Tart VMs scheduled by Orchard on `studio2`, with task-scoped homes, workspaces, simulators, skills, and MCP configuration.
3. **Decorrelated review** — prepare immutable review capsules, run independent read-only CLI lanes, normalize findings, and publish SHA-bound review evidence through Open SWE's existing review surfaces.

Do not add a backlog manager, fleet pressure controller, token allocator, general scheduler, custom merge queue, or replacement dashboard. Prefer GitHub checks, branch protection, static concurrency limits, and provider or sandbox lifecycle primitives.

## Approved executor kinds

Every Mobilyze Agent Definition selects exactly one approved executor kind. The definition also owns the behavior, model policy, instructions, schemas, limits, and named endpoint, profile, tool, or helper references used by that executor.

- **`api_model`** — Open SWE owns conversation state, model turns, tools, checkpoints, cancellation, and task results. The Agent Definition selects an approved provider/model and endpoint profile; Mobilyze code must not introduce a second agent loop.
- **`cli_agent`** — a provider CLI owns its internal agent loop. The Mobilyze harness owns the outer lifecycle, isolation, normalized status and artifacts, and continuity through the exact provider-generated session identity.
- **`external_helper`** — a pinned and hash-verified executable owns one bounded operation under an explicit input/output contract. It is not an agent loop or an implicit model-access path.

Endpoint profiles describe deployment metadata and capabilities; they are not separate agent implementations. Evidence and compatibility checks must bind the endpoint-profile identity and capability hash without changing graph or executor selection.

Task profiles own the sandbox or image, skills, MCP servers, permissions, credentials, and cleanup. API agents receive MCP capabilities through Open SWE's MCP bridge and task profile, not through Codex-native configuration.

No executor may be introduced implicitly or bypass the approved Agent Definition or any applicable endpoint/profile, provider-session, or helper identity that governs it. Direct or unclassified model calls remain prohibited; approved subscription-backed and official provider API access both use `api_model` rather than separate graph or executor kinds.

## Ownership boundaries

Mobilyze-owned files belong in these namespaces:

- `agent/mobilyze/**` — production extensions;
- `tests/mobilyze/**` — unit, contract, and integration tests for those extensions;
- `docs/mobilyze/**` — fork policy and operator documentation;
- `config/mobilyze/**` — small, versioned policy surfaces;
- `scripts/mobilyze/**` — deterministic developer and CI commands;
- `.github/workflows/mobilyze-*.yml` — fork-only CI jobs.

Upstream-owned files may be changed only as declared integration seams. A seam should import, register, or select a Mobilyze component; it should not contain provider logic, process management, VM lifecycle, review policy, or duplicated upstream behavior.

## Design rules

- One responsibility per module. Split by lifecycle owner or provider boundary rather than adding modes to a shared god object.
- Prefer explicit protocols and small dataclasses over inheritance trees or generic plugin frameworks.
- Keep provider adapters separate. Shared code may own process lifecycle and normalized events, not provider-specific flags or parsers.
- Implement async-only runtime paths. Do not add parallel sync implementations.
- Let Open SWE own the agent/tool loop for `api_model`; do not wrap approved API access in a provider-specific Mobilyze loop.
- For `cli_agent`, persist the opaque provider-generated session ID and resume that exact session. Apply this rule to another approved executor only when its provider explicitly exposes a resumable provider session; otherwise `api_model` and bounded `external_helper` execution do not require provider-session IDs.
- Never retry a side-effectful coding run blindly. Classify whether execution started, whether writes occurred, and whether an explicitly exposed provider session can resume.
- Treat sandbox/image selection, skills, MCP configuration, permissions, credentials, cleanup, and provider homes as task-profile inputs, not global host state.
- Read reviewer skills and policy from a trusted base SHA or administrator-controlled source, never an untrusted PR head.
- Disable optional model-backed features in Mobilyze mode until an approved Agent Definition selects their executor and named endpoint/profile/helper identities.

## Upstream integration policy

Every PR that changes an upstream-owned path must add or update its entry in `config/mobilyze/architecture-guardrails.json`. Each entry records the seam's purpose and an added-line budget. The budget is a ceiling, not a target.

Upstream synchronization is not an excuse to disable ordinary checks. A dedicated sync workflow will prove that incoming commits are upstream-derived and will report the resulting Mobilyze delta. Until that workflow lands, upstream syncs require explicit review and temporary waivers where necessary.

## File-growth policy

The architecture guard enforces:

- new non-exempt custom source files: at most 350 lines;
- custom source files already at or above 600 lines: no growth;
- undeclared changes to upstream-owned paths: rejected;
- declared upstream seams: per-PR added-line budget;
- waivers: explicit reason and expiration date, printed by CI even when active.

Tests, documentation, and generated output are exempt from source line limits, but not from ownership boundaries.

Run locally:

```bash
python scripts/mobilyze/check_architecture.py --base-ref origin/main
python -m unittest tests/mobilyze/test_architecture_guard.py
```

A waiver belongs in `config/mobilyze/architecture-guardrails.json`:

```json
{
  "path": "agent/mobilyze/example.py",
  "rule": "file_size.new_file_line_cap",
  "reason": "OSWE-123: temporary exception while the parser is split",
  "expires": "2026-08-31"
}
```

Waivers are temporary debt records. Expired or malformed waivers do not suppress failures.

## Review checklist

Before merging a Mobilyze change, confirm:

1. The change belongs to one of the three permitted extension areas.
2. New behavior lives in a Mobilyze-owned namespace.
3. Upstream file edits are narrow declared seams.
4. The architecture guard and focused tests pass.
5. Every model or helper call uses an explicit `api_model`, `cli_agent`, or `external_helper` definition and its approved identities; direct or unclassified calls remain prohibited.
6. Endpoint-profile identity and capability hashes are present where evidence or compatibility decisions depend on them, without selecting a different graph.
7. API-agent MCP capabilities come from Open SWE's MCP bridge and task profile, not Codex-native configuration.
8. No new orchestration subsystem was created where Open SWE, GitHub, Orchard, Tart, a provider CLI, or a pinned helper already owns the lifecycle.
