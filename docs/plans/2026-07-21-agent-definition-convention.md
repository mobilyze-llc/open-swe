# Decision: agent definitions as files — the additive `agents/` convention

- **Status:** Accepted (Eric Litman, 2026-07-21)
- **Ticket:** OSWE-33 (re-contracted; this doc supersedes the 2026-07-19 "Agent Definition v1 / execution plans" spec preserved in that issue's history)
- **Kind:** point-in-time decision record. Frozen; changes require a new dated doc.

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

1. **Repo-root `agents/` holds Mobilyze-defined agents only.** The boundary of the
   convention is the fork seam. Upstream's three graphs (main, reviewer, analyzer)
   are never restructured in the fork; changes to them travel via upstream PRs
   exclusively. The implementation diff against upstream is additive files plus the
   permitted seam touches (`langgraph.json` registration, webhook routing).
2. `agents/<name>/` pairs by name with the existing `evals/<name>/` harness
   convention (`evals/reviewer/` predates this decision).

### Shape

3. **An agent is a named directory; `agent.md` is required.** One form only — no
   bare-file variant. Frontmatter is a closed set:
   - `description` — purpose; for subagents this is the delegation signal used by
     the `task` tool.
   - `tools` — names resolved against the curated registry
     (`agent/tools/__init__.py`). Unknown names are load errors.
   - `mcp` — server → tool-allowlist references resolved against a small
     name → loader registry over the existing `agent/integrations` idiom.
   - `env` — CLI-lane agents only: environment variable **names**, never values.

   Body = the system prompt template (existing `str.format` placeholders allowed).
4. **`subagents/*.md` hydrate deepagents `SubAgent` specs.** The directory listing
   is the roster — no roster field in `agent.md` (no double-entry bookkeeping).
5. **`shared.md` is the single composition slot.** When present it is prepended to
   `agent.md`'s body and every subagent body in the directory. This exists because
   duplicated content (e.g. a review-findings bar) drifts, and drift across
   personas is what makes multi-agent output incoherent. There is **no other
   composition mechanism**: no named partials, no includes syntax, no conditionals.
6. **Three prompt layers, two of which already exist:** fleet voice via the harness
   profile base (upstream, untouched) → workflow-scoped `shared.md` (this
   convention) → per-file body.

### Exclusions (deliberate)

7. **No `model` in frontmatter.** Model resolution stays entirely in the runtime
   chain. Keeping model out of definitions is what allows A/B-ing models against a
   fixed prompt artifact — prompt/model orthogonality is a goal, not an accident.
   (Eve pins the model in `agent.ts`; this stack is deliberately ahead of Eve here.)
8. **No `name` field.** The directory name is the identity.
9. **No secrets or connection config in files.** Credentials remain encrypted in
   team settings / per-user OAuth; MCP transports, headers, and timeouts remain in
   the loader modules. Frontmatter says *may use*; runtime decides *available*.
10. **The loader parses and validates only.** Closed schema, unknown fields
    rejected, all references validated, deterministic error reporting, import/
    factory-time loading. It never imports code from definition directories, never
    makes runtime decisions, and there is no hot reload — releases are immutable;
    the live-tweak channel remains the DB-backed style prompts.

### Eve directory taxonomy, mapped

| Eve | Verdict | Why |
|---|---|---|
| `subagents/` | **Adopt** | Core of this convention. |
| `connections/` | **Adopt declaratively** | `mcp:` references over the existing loader idiom; no OpenAPI→tools generation. |
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

A **new** graph (`agents/reviewer-adversarial/` name TBD at implementation) beside
the stock upstream reviewer — persona subagents (e.g. security, correctness) plus
an adjudicator, publishing through the existing findings tools. Two topologies are
both expressible in the layout, and choosing between them is the first eval
experiment the structure exists to make cheap:

- **Parent-as-adjudicator** — personas return candidates; the parent (already
  holding candidates + diff) cross-examines and publishes. No extra pass, but
  anchored on finder reasoning.
- **Adjudicator-as-subagent** — a fresh-context refuter per candidate batch.
  Decorrelated, higher precision expected; each subagent pass is a cold
  ~0.7–1.6M-real-token context establishment (canary-2 baseline), and pass count
  is the budget lever.

Switching topologies is adding/removing `subagents/adjudicator.md` plus a
paragraph in `agent.md`; `evals/reviewer/` (goldens + judge) measures the
precision delta. The persona roster being visible in `ls` makes a directory
review double as a budget review.

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

Any rung that lands lets the fork converge on one convention with zero delta. Per
the standing bar, every upstream submission gets an adversarial maintainer-caliber
pass first. If upstream adopts prompts-as-files in any form, the fork migrates to
**their** form immediately.

## Tripwires (conditions that reopen this decision)

- A definition file wants a conditional, a reference to another file, or any
  expression of *new behavior* rather than selection among existing behaviors →
  that logic is Python; stop extending the format.
- `shared.md` sprouts named partials or per-consumer variants → templating
  language; stop.
- The loader needs its own correctness reasoning (caching, normalization,
  selective rerun) → it has become a framework; cut.
