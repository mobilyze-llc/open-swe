# Mobilyze Open SWE fork architecture

This repository is a thin fork of `langchain-ai/open-swe`. Upstream Open SWE owns the control plane: webhooks, authentication, threads, dashboard, sandbox lifecycle, GitHub and Linear integrations, findings storage, and review publication.

Mobilyze code exists only where product requirements cannot be met upstream without a narrow extension.

## Permitted extension areas

1. **Native CLI harness execution** — invoke coding agents through provider CLIs such as `claude -p` and `codex exec`, normalize their events, and persist provider session identities.
2. **Apple execution** — route iOS and macOS work to Tart VMs scheduled by Orchard on `studio2`, with task-scoped homes, workspaces, simulators, skills, and MCP configuration.
3. **Decorrelated review** — prepare immutable review capsules, run independent read-only CLI lanes, normalize findings, and publish SHA-bound review evidence through Open SWE's existing review surfaces.

Do not add a backlog manager, fleet pressure controller, token allocator, general scheduler, custom merge queue, or replacement dashboard. Prefer GitHub checks, branch protection, static concurrency limits, and provider or sandbox lifecycle primitives.

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
- Persist opaque provider-generated session IDs instead of requiring caller-generated IDs.
- Never retry a side-effectful coding run blindly. Classify whether execution started, whether writes occurred, and whether the provider session can resume.
- Treat skills, MCP configuration, credentials, and provider homes as task-profile inputs staged inside the sandbox, not global host state.
- Read reviewer skills and policy from a trusted base SHA or administrator-controlled source, never an untrusted PR head.
- Disable optional model-backed features in Mobilyze mode until they use the approved CLI harness path.

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
5. No direct provider API model call was introduced in Mobilyze mode.
6. No new orchestration subsystem was created where Open SWE, GitHub, Orchard, Tart, or the provider CLI already owns the lifecycle.
