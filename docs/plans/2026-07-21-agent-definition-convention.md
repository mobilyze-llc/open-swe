# Decision: agent definitions as files — the additive agent-directory convention

- **Status:** Accepted rev 2 (Eric Litman, 2026-07-21; revised same-day after a decorrelated Codex adversarial review returned NOT SHIPPABLE on rev 1 — disposition appendix at bottom; rev 1 is commit `c04f28419`)
- **Ticket:** OSWE-33 (re-contracted; this doc supersedes the 2026-07-19 "Agent Definition v1 / execution plans" spec preserved in that issue's history)
- **Kind:** point-in-time decision record. Frozen after rev 2; further changes require a new dated doc.

## Context

Mobilyze wants agent definitions (prompts, tool rosters, subagent personas) to live
as files rather than Python string constants, for four concrete goals: reviewable
prompt diffs, evals against stable prompt artifacts, cheap experimentation with
multi-agent topologies, and reuse of shared prompt content without drift. The
inspiration is Eve's filesystem-first model (eve.dev); the counter-pressure is this
repo's constitution as a **thin fork** of `langchain-ai/open-swe` (fork delta =
model-gateway seam + OSWE-55 only) and the July 2026 bloat failure, whose artifact —
the original OSWE-33 spec with executor-kind unions, an execution-plan compiler,
content hashes, and fixture replay — is the cautionary tale this decision replaces.

Inventory that shaped the decision (all upstream-owned):

- Prompts-as-files already exists narrowly: `agent/resources/default_prompt.md`
  loaded via `importlib.resources`, overridable with `DEFAULT_PROMPT_PATH`
  (`agent/prompt.py`).
- The fleet-shared base prompt is registered per provider as a deepagents
  `HarnessProfile.base_system_prompt` (`agent/prompt.py`), which subagents inherit
  automatically. deepagents 0.7.0a7 also ships `HarnessProfileConfig` — a beta,
  YAML/JSON-loadable declarative profile — signaling LangChain's own direction.
- Subagents are already declarative `SubAgent` dicts (`name`, `description`,
  `system_prompt`, `tools`, `model`, `skills`) in `agent/server.py` and
  `agent/reviewer.py`; they are just constructed in Python instead of loaded.
- Skills are filesystem-first today (`agent/skills/*/SKILL.md`, `SkillsMiddleware`).
- MCP attachment has a house idiom: per-server loader modules in
  `agent/integrations/*_mcp.py` (credentials encrypted in team settings or per-user
  OAuth, `MultiServerMCPClient`, degrade-to-empty, TTL-cached) spliced into tool
  lists at factory time.
- Model/effort resolution is already externalized (per-thread config → dashboard
  profile → team default) and deliberately runtime.
- Dynamic prompt assembly is a render pipeline, not a document: the reviewer prompt
  splices ~10 runtime inputs (org guidelines, learned repo style, AGENTS.md, trace
  context, first-vs-re-review context, …). That assembly is code and stays code.

## Decision

### Boundary

1. **`agent/resources/agents/` holds Mobilyze-defined agents only.** The boundary
   of the convention is the fork seam. Upstream's graphs are never restructured in
   the fork; changes to them travel via upstream PRs exclusively. The
   implementation diff against the fork base is additive files plus the permitted
   seam touches (`langgraph.json` registration, webhook routing).

   *Rev 2 note — why packaged, not repo-root:* `langgraph.json` declares
   `dependencies: ["."]`, so the runtime pip-installs the project, and the wheel
   packages only `agent/` (`pyproject.toml` `packages = ["agent"]`). A repo-root
   `agents/` directory exists in source checkouts but not in the installed
   package — definitions would silently vanish in exactly the deployment path
   that matters. `agent/resources/` is already packaged and already loaded via
   `importlib.resources` (the `default_prompt.md` idiom), so definitions live at
   `agent/resources/agents/<name>/`.
2. `agent/resources/agents/<name>/` pairs by name with the existing
   `evals/<name>/` harness convention (`evals/reviewer/` predates this decision).

### Shape

3. **An agent is a named directory; `agent.md` is required.** One form only — no
   bare-file variant. Frontmatter is a closed set of exactly two keys:
   - `description` — purpose; for subagents this is the delegation signal used by
     the `task` tool.
   - `tools` — names resolved against the curated registry
     (`agent/tools/__init__.py`). Unknown names are load errors. **Omission means
     `tools: []`, never parent inheritance** — deepagents treats an absent
     `SubAgent.tools` key as "inherit the parent's tools," which would hand
     reviewer personas `add_finding`/`publish_review`/HTTP and destructive Linear
     tools. The factory translates omission to an empty list and enforces a
     per-graph capability ceiling; finding-mutation and publication tools are
     reserved to the parent. Prompt text is not an authorization boundary.

   Body = the system prompt template (existing `str.format` placeholders allowed).
   (Rev 2: the `mcp` and `env` keys were cut from v1 — see Deferred below.)
4. **`subagents/*.md` hydrate deepagents `SubAgent` specs.** The directory listing
   is the roster — no roster field in `agent.md` (no double-entry bookkeeping).
5. **`shared.md` is the single composition slot.** When present it is prepended to
   `agent.md`'s body and every subagent body in the directory. This exists because
   duplicated content (e.g. a review-findings bar) drifts, and drift across
   personas is what makes multi-agent output incoherent. There is **no other
   composition mechanism**: no named partials, no includes syntax, no conditionals.
6. **Prompt assembly is owned by the factory — the fleet base is NOT inherited for
   free.** Rev 1 claimed the harness-profile base layers under persona bodies
   automatically. It does the opposite: deepagents' `_apply_profile_prompt` runs
   uniformly over declarative subagents with `base_prompt=spec["system_prompt"]`,
   and a registered `base_system_prompt` **replaces** that prompt outright — under
   the Open SWE per-provider profiles, every file-defined persona would silently
   become `OPEN_SWE_SHARED_BASE`. The convention therefore requires: the
   loader/factory assembles the full subagent prompt itself (fleet base +
   `shared.md` + persona body) and guarantees — with a test that inspects the
   effective system prompt — that persona text actually reaches the model under
   the registered profiles. How (unregistered model spec, `CompiledSubAgent`, or
   equivalent) is an implementation choice; the assembled-prompt-reaches-the-model
   property is the requirement.

### Exclusions (deliberate)

7. **No `model` in frontmatter.** Model resolution stays entirely in the runtime
   chain. Keeping model out of definitions is what allows A/B-ing models against a
   fixed prompt artifact — prompt/model orthogonality is a goal, not an accident.
   (Eve pins the model in `agent.ts`; this stack is deliberately ahead of Eve here.)
   Precision (rev 2): "the runtime chain" is per-graph-family, and the reviewer's
   differs from the main agent's. The main agent resolves per-thread config →
   dashboard profile (`load_profile` by GitHub login) → team default; the reviewer
   family resolves `reviewer_model_id`/`reviewer_reasoning_effort` (and separately
   `reviewer_subagent_model_id`/`reviewer_subagent_reasoning_effort`) → team
   reviewer default pair — **no profile step**. The new graph follows the reviewer
   family's chain with its own configurable keys, named in the contract, so parent
   and persona models stay independently overridable for evals.
8. **No `name` field.** The directory name is the identity.
9. **No secrets or connection config in files.** Credentials remain encrypted in
   team settings / per-user OAuth; MCP transports, headers, and timeouts remain in
   the loader modules.
10. **Deferred out of v1 (rev 2): the `mcp` and `env` frontmatter keys.** Neither
    has a consumer — the adversarial reviewer uses no MCP tools, and `env` exists
    only for the not-yet-contracted CLI lane — so v1 shipping them would be unused
    extension surface. The review also surfaced hazards the future design must
    answer before the `mcp` key returns: per-server tool names are discovered
    asynchronously at connect time (a static allowlist can't be load-validated),
    existing loaders degrade to empty on failure, and credential scope differs per
    server (Notion is user-bound via `profile_login`, Datadog uses team
    credentials, Corridor is deployment-global) — "configured" must not be
    conflated with "authorized," or a malicious PR under review could reach team-
    or user-scoped data tools. The declaration-vs-materialization split
    (frontmatter says *may use*; runtime decides *available*) remains the intended
    shape when a consumer arrives.
11. **The loader parses and validates only.** Closed schema, unknown fields
    rejected, all references validated, deterministic error reporting, import/
    factory-time loading. It never imports code from definition directories, never
    makes runtime decisions, and there is no hot reload — releases are immutable;
    the live-tweak channel remains the DB-backed style prompts.

### Eve directory taxonomy, mapped

| Eve | Verdict | Why |
|---|---|---|
| `subagents/` | **Adopt** | Core of this convention. |
| `connections/` | **Defer** (rev 2) | Intended shape: `mcp:` references over the existing loader idiom — but no v1 consumer, and open hazards (async tool discovery, credential-scope divergence); no OpenAPI→tools generation ever. |
| `hooks/` | Decline | This is langchain middleware — ordered, behavioral, code. Hooks-as-data would import code from data dirs and make agent self-modification unreviewable. Ceiling if ever needed: deepagents `excluded_middleware` by name. |
| `sandbox/` | Decline | `ensure_sandbox_for_thread` is a stronger existing version; no per-agent knob has a consumer. |
| `schedules/` | Decline | Scheduling here is runtime data (per-repo analyzer crons, `schedule_thread_wakeup`), not static agent facts. |
| `lib/` | Decline | Python already has a module system (`agent/utils/`). |

### CLI-lane agents (future ticket, constrained here)

When the CLI-agent lane lands (invoking Anthropic/other models through their own
CLIs), those agent directories ship the CLI's **native config verbatim** (e.g.
`claude/.mcp.json`, `claude/settings.json`) plus `env` names; the launcher
materializes files and environment into the sandbox. **No unified executor schema
will be introduced** — two executor kinds, two small consumers, sharing only the
directory convention. This is the explicit anti-pattern boundary against the
superseded execution-plan compiler.

## First consumer: adversarial reviewer

A **new** graph (`agent/resources/agents/reviewer-adversarial/`, name TBD at
implementation) beside the stock upstream reviewer — persona subagents (e.g.
security, correctness) returning candidates, the **parent acting as adjudicator**
(cross-examining candidates against the diff before recording), publishing
through the existing findings tools.

*Rev 2 scope cut:* rev 1 shipped both adjudicator topologies and chose "by eval,"
but defined no decision rule — no precision threshold, no budget ceiling, no
owner — and the existing eval target reports findings and judge scores, not
subagent pass counts or real-token spend, so identical results could justify
opposite topologies. v1 therefore ships **parent-as-adjudicator only** (it
matches the stock reviewer's existing parent-validation flow). The decorrelated
fresh-context-refuter variant is preserved as a follow-up experiment ticket
(sub-issue of OSWE-33) whose contract must state the decision rule up front:
metrics (judge precision on `evals/reviewer/` goldens, pass count, real-token
cost vs the canary-2 baseline of ~0.7–1.6M real tokens per cold subagent pass),
the comparison threshold, and Eric as decision owner. Proving the file
convention does not require implementing both orchestrations.

The persona roster being visible in `ls` still makes a directory review double
as a budget review.

## Rejected alternatives

- **Dawn / meta-framework adoption** — heavier than the goals require; wrong
  language; duplicates what LangGraph already owns (workflow logic is graphs).
- **The superseded OSWE-33 spec** (closed executor-kind schema, `ApiModelPlan`/
  `CliAgentPlan`/`ExternalHelperPlan` compilation, stable hashes, fixture replay,
  references to a `HarnessSpec` that exists nowhere in the code) — the
  fourth-generation framework of the July bloat failure.
- **Migrating upstream graphs into `agents/` in the fork** — permanent merge tax
  on files upstream actively iterates (`prompt.py`, `reviewer.py`). Convergence
  runs through upstream PRs or not at all.
- **Templating (includes, partials, conditionals), hot reload, registry services,
  frontmatter `model`** — each is the framework tripwire in a different costume.

## Upstream ladder (tracked as OSWE-33 sub-issues)

1. **Small:** propose upstream move of `REVIEWER_PROMPT_TEMPLATE` (and candidate
   `prompt.py` section constants) into `agent/resources/*.md` loaded via
   `importlib.resources` — their own `default_prompt.md` idiom applied again.
2. **Medium:** propose a declarative subagent loader (markdown+frontmatter →
   `SubAgent`) in `langchain-ai/deepagents`, aligned with `HarnessProfileConfig`'s
   declarative direction.
3. **Large (not pursued):** upstream adoption of the `agents/` directory
   convention wholesale.

Rev 2 correction: no rung yields **zero** delta — each removes a specific slice
of fork surface and leaves the rest. Rung 1 removes the fork's interest in
carrying prompt-file relocations but leaves `agent.md`/`shared.md`, the loader,
the factory, and registration in the fork. Rung 2 removes the fork's subagent
loader **only if** the deepagents proposal stays generic — markdown+frontmatter →
`SubAgent`, nothing more; open-swe-specific keys or semantics would (rightly) be
rejected by a deepagents maintainer, so OSWE-74 proposes the generic core only.
Per the standing bar, every upstream submission gets an adversarial
maintainer-caliber pass first. If upstream adopts prompts-as-files in any form,
the fork migrates to **their** form immediately.

## Tripwires (conditions that reopen this decision)

- A definition file wants a conditional, a reference to another file, or any
  expression of *new behavior* rather than selection among existing behaviors →
  that logic is Python; stop extending the format.
- `shared.md` sprouts named partials or per-consumer variants → templating
  language; stop.
- The loader needs its own correctness reasoning (caching, normalization,
  selective rerun) → it has become a framework; cut.

## Appendix: adversarial review disposition (2026-07-21, rev 2)

Decorrelated Codex review (GPT-family, via the codex plugin runtime; cmux
substrate was down on both hosts and is deprecated) returned **NOT SHIPPABLE**
on rev 1 with 7 P1 findings and 1 P2. Disposition after independent
verification of each claim against the repo and the installed deepagents wheel:

| # | Finding (P) | Verdict | Disposition in rev 2 |
|---|---|---|---|
| 1 | Harness profile **replaces** raw `SubAgent` prompts — personas silently clobbered (P1, critical) | Confirmed in `_apply_profile_prompt` source | Prompt assembly owned by factory; effective-prompt test required (Decision §6) |
| 2 | Repo-root `agents/` absent from installed package (`dependencies: ["."]` + wheel packages only `agent/`) (P1) | Confirmed | Definitions moved to `agent/resources/agents/`, `importlib.resources` idiom (§1) |
| 3 | `mcp` registry has no safe load contract and no v1 consumer (P1) | Accepted (matches anti-bloat bar) | `mcp` + `env` keys deferred out of v1 with hazards recorded (§10) |
| 4 | Omitted subagent `tools` inherits privileged parent tools (P1) | Confirmed in `SubAgent` semantics | Omission ⇒ `[]`; capability ceiling; publication tools parent-only (§3) |
| 5 | Reviewer model chain misstated (no profile step; separate parent/subagent keys) (P1) | Confirmed in `get_reviewer_agent` | Reviewer-family chain named precisely (§7) |
| 6 | `git diff upstream/main` AC uses wrong baseline (24 pre-existing fork paths) (P1) | Confirmed | AC re-anchored to fork-base…HEAD (contract) |
| 7 | Topology bake-off lacks decision rule; both topologies unnecessary for v1 (P1) | Accepted in part | v1 ships parent-as-adjudicator; refuter experiment split to sub-issue with mandatory decision rule (First consumer) |
| 8 | "Zero delta" upstream-convergence overclaim; deepagents rung must stay generic (P2) | Accepted | Residual-surface accounting; OSWE-74 scoped generic-only (Upstream ladder) |

Review artifact: session task `b4izsg8oj` output (codex thread
`019f8562-a9ab-7e53-aa94-29da0bf4e236`). Findings 2 and 7 reverse rev-1 choices
the operator had approved (repo-root location; dual-topology bake-off) — flagged
for explicit operator veto in the session that produced this revision.
