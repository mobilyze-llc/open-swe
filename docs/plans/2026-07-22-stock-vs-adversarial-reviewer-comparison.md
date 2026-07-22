# Stock reviewer vs reviewer-adversarial: mechanical and structural catalog

**Date:** 2026-07-22 (second pass same day, re-verified @ `af768adca`) · **Tracker:** OSWE-33
follow-on · **Status:** scoping artifact for the v2 / default-adversarial work (no code changes)

Compares the reviewer that ships with Open SWE (`agent/reviewer.py`, evolved through upstream PRs
#1690–#1765) against the OSWE-33 adversarial reviewer (`agent/reviewer_adversarial.py` +
`agent/reviewer-adversarial/*.md`, fork PRs #24/#26). The goal is a menu of tactics the stock
reviewer uses that the adversarial graph does not, plus divergences discovered along the way.
Sections 1–9 were drafted against `d3db1df18`; §10–§13 extend the catalog for the OSWE-76
stage-profiles merge (`af768adca`), the deployment-routing verification, and the v2 scoping map.
All file:line anchors are corrected to `af768adca`.

The headline: the two graphs share far more than the naming suggests. The adversarial module
deliberately imports the stock reviewer's private helpers, reuses its entire tool set, its entire
middleware stack, and the whole findings/publish/diff library. Most of the stock reviewer's
mechanical tactics — shell prep, token proxying, publish retries, check-run settlement — are
therefore already active in adversarial runs. The real gaps are (1) the prep-time context inputs v1
deliberately omitted, (2) the re-review / finding-reply lifecycle, and (3) a specific set of
prompt-level tactics. Three genuine discrepancies also surfaced (§7).

---

## 1. What is shared (already incorporated, nothing to port)

- **Tools:** identical ten-tool set (`fetch_review_diff`, `add_finding`, `update_finding`,
  `list_findings`, `publish_review`, `resolve_finding_thread`, `reply_to_finding_thread`,
  `web_search`, `fetch_url`, `http_request`). Adversarial's come from `agent.md` frontmatter,
  validated against the curated registry at import.
- **Middleware:** identical twelve-entry stack in identical order (prepare-run, sanitize inputs,
  model-call limit, tool-error, proxy refresh, message queue, Slack status, timeout wrap-up,
  Fireworks/thinking sanitizers, orphaned-tool-call repair, settle-review-check).
- **Sandbox lifecycle:** both call `_ensure_reviewer_sandbox_for_thread` → GitHub App installation
  token minted per run scoped to the one repo, cached per-thread as a bot token, proxy configured.
- **Run context:** both use `_build_first_review_context` (PR coordinates + XML-wrapped title/body)
  and `_repo_checkout_note`; both append `REVIEWER_EVAL_PROMPT_SUFFIX` in eval mode; both honor the
  eval dry-run publish path.
- **Model plumbing:** same team-default resolution, Fable gating, gateway flag, deferred-error
  model construction. Adversarial adds namespaced `reviewer_adversarial_*` config keys that fall
  back to `reviewer_*` keys only in eval mode.

The import-the-privates choice (`_build_first_review_context`, `_cached_gateway_enabled`,
`_cached_reviewer_team_defaults`, `_ensure_reviewer_sandbox_for_thread`, `_make_model_or_defer`,
`_repo_checkout_note`) is intentional: an upstream rename breaks the module loudly instead of
letting behavior drift.

## 2. Shell-command tactics

**Shared — deterministic pre-run repo prep** (`agent/utils/repo_prep.py`). Both graphs run the same
prep script before the first model call so the LLM never narrates cloning: `set -e`; clone-or-fetch
(`GH_TOKEN=dummy gh repo clone` when no `.git`, tolerated `git fetch --all || true` when reusing);
targeted base and head fetches each `2>/dev/null || true`; a fork-PR fallback fetch of
`refs/pull/<n>/head` (fork head commits aren't reachable from origin branches); `git checkout
--force` because a reused sandbox can hold a dirty worktree that would silently block checkout; and
a hard `[ "$(git rev-parse HEAD)" = <head_sha> ]` verification so a failed checkout fails the prep
rather than leaving a stale tree that looks prepped. 240 s timeout, `shlex.quote` on every
interpolation. (Correction, PR #29 review: repo-name *regex* validation is tool-side —
`_REPO_NAME_RE` in `fetch_review_diff` — not in `prepare_review_repo`, which only rejects empty
owner/name; `_valid_repo_component` guards only the main-agent skills path.) On failure the system
prompt flips to the recovery note.

**Shared — the recovery note** (`_REPO_NOT_READY_NOTE`, `agent/reviewer.py:388`). When prep fails,
the prompt embeds an exact re-prep script (clone-or-cd, sha fetch with pull-ref fallback, forced
checkout, rev-parse verification) plus the last-resort discipline: if the tree can't reach the PR
head, rely exclusively on the diff and `gh api repos/<o>/<r>/contents/<path>?ref=<sha>` — never on
the local checkout.

**Shared — diff materialization** (`fetch_review_diff` tool + `agent/review/diff.py`). The diff is
computed in the sandbox as `git diff --no-color base...head` — three-dot merge-base on first review
(matches GitHub's Files-changed tab, immune to base-branch drift), two-dot on re-review deltas —
written to a deterministic content-addressed path (`review-diff-<sha16>.patch`, cached across
calls), and the tool returns only bounded metadata (path, byte count, file list capped at 200).
Both prompts carry the matching discipline: inspect the file with `grep` and paginated `read_file`;
never fetch a full diff through `execute` or `gh`.

**Stock-only prompt tactics:**
- `git show <base_sha>:path` base-vs-head comparison on refactors (workflow pass 3), hunting
  silently dropped nil-checks, error handling, async-ness, lock scope, transactions.
- The dependency-install policy line: install packages only when needed to verify the PR, using the
  project's package manager. Absent from every adversarial prompt.
- Trusted-skills extraction (stock-only feature, §5): `git cat-file -e <ref>:<dir>` existence gate,
  `git archive <ref> <dir> | tar -x --strip-components=N` into a directory outside the checkout,
  `.trusted-ref` cache marker, `chmod -R a-w` — skills sourced from the base sha so a PR author
  can't inject reviewer instructions via a `SKILL.md`.

**Adversarial-only:** finder personas are explicitly instructed to work the checkout with
`read_file`/`grep`/`execute` rather than reasoning from the diff alone — stock's subagent prompt
has no equivalent instruction.

## 3. Token and auth tactics

Fully shared; no divergence anywhere in the code path.

- **`GH_TOKEN=dummy` convention:** the LangSmith sandbox proxy injects Basic auth for `github.com`
  git traffic and Bearer for `api.github.com`; the dummy value only satisfies `gh`'s
  token-presence check. Real tokens never enter sandbox env or prompt text.
- **Scoped minting:** installation tokens are requested with `repositories=[repo]` so a sandbox
  never holds credentials broader than the PR under review.
- **Mid-run refresh:** `refresh_github_proxy_before_model` re-configures the proxy when the
  recorded token is within 5 min of its 1-hour expiry (50-min fallback TTL when expiry is unknown),
  and the refresh preserves the original repository/permission scope so it can't broaden.
- **401 handling:** `publish_review` maps a 401 to `GitHubAuthError`, invalidates the per-thread
  cached token, and returns a structured "re-authenticate and trigger again" error instead of
  letting the agent retry.

## 4. Findings lifecycle ("ticket filing")

Neither reviewer files Linear tickets — the findings model *is* the reviewer's ticket system
(`linear_*` tools are main-agent only). The machinery is shared end to end; the differences are
prompt-guidance depth and which lifecycle events each graph accepts.

**Shared storage and creation** (`agent/review/findings.py`, `agent/tools/add_finding.py`):
findings persist in LangGraph thread metadata (survives sandbox eviction, queryable cross-run);
per-thread asyncio mutation locks around read-modify-write; content fingerprints dedupe re-adds
(`duplicate: true` instead of a second record); in-diff validation at creation time against
LEFT/RIGHT line sets (with a fallback chain: injected state → configurable → re-fetch the API diff);
the relevant diff hunk is stashed on the finding for UI rendering; titles are normalized (empty or
default titles rejected, 120-char cap); suggestions are clipped at 4 lines with a structured
warning rather than silently truncated; a missing findings thread returns the `thread_not_found`
do-not-retry contract. `resolve_review_head_sha` prefers the metadata head over the run's frozen
config so a push that lands mid-run anchors the review to the commit actually reviewed.

**Shared publication** (`agent/tools/publish_review.py`, `agent/review/publish.py`): severity
threshold (default `medium`) plus cap 6 via `filter_findings_for_publish`; the review body is
host-formatted (the agent never writes review prose — "found N potential issue(s)" or "no issues
found", plus web/trace links); every inline comment embeds a hidden
`<!-- open-swe-review-comment {json} -->` marker that is the *sole* source of truth for mapping
GitHub comment ids back to findings; a 422 "Path/Line could not be resolved" is detected as
`unresolved_anchor`, the offending findings are pruned against a re-fetched diff line set, and the
post is retried exactly once — surviving drops come back as `unresolvable_findings` with a hint,
which both prompts translate into "repair via `update_finding`, then publish again, never retry
byte-identical". Publication identity (review id + comment ids) is stamped in a single atomic
findings write, with marker-based backfill from PR threads if stamping raced a crash. Resolved and
dismissed findings get their GitHub threads closed via the GraphQL `resolveReviewThread` mutation,
with the agent-authored `note` posted verbatim as the reply. Empty re-reviews are suppressed via a
tri-state check (durable `re_review` flag → durable `last_reviewed_sha` → GitHub marker scan, where
API failure means "unknown, do not suppress"). Around the review itself: a transient "review in
progress" status comment posted/reused/deleted; the `Open SWE Review` check run settled with a
pending-result retry path; a Slack completion reply when the review came from Slack; and auto-fix
dispatch back to the implementation thread (cycle cap 2, fork-headed PRs fail closed, per-repo /
per-PR / per-user opt-outs, cycle counted only after dispatch succeeds).

**Shared learning hook:** `update_finding` / `resolve_finding_thread` emit finding-status outcomes
(`emit_finding_status_outcome`) that the continual-learning analyzer reads. Note the asymmetry in
§5: adversarial runs *write* to this loop but don't *read* the style prompt it produces.

**Divergences:**
- Stock's prompt carries full re-review and finding-reply choreography (resolve vs dismiss
  semantics, note-posted-verbatim contract, when to use `reply_to_finding_thread` vs letting the
  system post). Adversarial v1 rejects those dispatches outright at prep
  (`agent/reviewer_adversarial.py:146`) — correct for v1, since the shared tools read `re_review` /
  `last_reviewed_sha` themselves and would apply semantics the rendered prompt doesn't describe.
- Consequence: `resolve_finding_thread` and `reply_to_finding_thread` are wired into the
  adversarial parent with zero usage guidance — dead surface until v2 grows those flows.
- Stock's pre-publish checklist is deeper: the zero-findings sanity check ("silence on a real
  change is usually a miss"), the no-two-findings-in-one-file-unless-independent-failure-modes
  rule, and the PR-title / top-changed-directories cross-check. Adversarial has rank, dedupe, cap.

## 5. Failure recovery and retries

**Shared:**
- `github_request` (`agent/utils/github_http.py`): exponential backoff with jitter, max 3 retries,
  `Retry-After` respected, secondary-rate-limit detection (403 + remaining 0 or body marker),
  429/503 retried for any method, 502/504 only for idempotent methods, transport errors likewise
  idempotency-gated. Every findings/publish API call flows through it.
- `_make_model_or_defer`: model construction failure at graph build becomes a deferred-error model
  so the failure surfaces inside the run, not as a boot crash.
- `BasePrepareRunMiddleware`: run prep is checkpointed behind a fingerprint latch (latest message +
  config identity), so a resumed attempt skips completed setup while a new invocation on the same
  thread re-preps fresh tokens/prompts/diff. Prep must stay idempotent.
- `RepairOrphanedToolCallsMiddleware`: inserts synthetic error `ToolMessage`s for tool calls that
  died mid-flight (cancelled run, dead sandbox), un-wedging threads the provider would otherwise
  reject forever.
- `TimeoutWrapupMiddleware`: after 45 min (env-tunable) injects a wrap-up-now instruction into the
  system message.
- `ModelCallLimitMiddleware(exit_behavior="end")` and `settle_review_check_on_exit`: runs that die
  without publishing still settle the check run — honoring a persisted pending result so a
  transient PATCH failure after a successful publish is retried with the *real* conclusion instead
  of misreported as failure.
- Diff materialization falls back from sandbox `git diff` to the API-fetched diff on error, in both
  prep implementations; sandbox backends reconnect through `get_cached_sandbox_backend(reconnect=…)`.

**Stock-only:** graceful per-input degradation at prep (threads fetch, trace context, AGENTS.md
each log-and-continue); background-task exception harvesting for the grouping pass; TTL caches on
org guidelines and the API-standards skill (300 s).

**Neither reviewer** carries `SandboxCircuitBreakerMiddleware` or `ensure_no_empty_msg` — those are
main-agent middleware; the reviewer graphs rely on the model-call limit plus the settle hooks.

## 6. Prompt structure and content

**Topology.** Stock's parent does the review itself through a nine-pass ordered workflow (literal
changed-line pass → diff end-to-end → base-vs-head on refactors → grep-beyond-diff on contract
changes → security/trust boundaries → CI/CD test enforcement → library-contract verification →
repo-conventions pass → new-dependency verification) and may delegate *at most one* subagent pass
over an explicit disjoint file list, validating the candidates itself. Adversarial's parent is a
pure orchestrator: dispatch every finder persona exactly once (parallel allowed), merge duplicates,
send the batch to the adjudicator, confirm surviving plausibles itself, publish. Only the parent
holds findings tools — enforced structurally (§ below), not just by prompt.

**What prompts v2 already ported into the finders** (no action needed): literal changed-line
priority, deleted-invariant tracking, grep-implementers-and-callers on contract changes, reachable
concurrency triggers, CI/test-enforcement weakening, plus a second-tier footgun list stock never
had (default computed once at definition, non-deterministic ordering/hashing, narrowed lock scope,
side-effectful predicate calls, setup/teardown asymmetry, flipped config defaults). The security
persona covers stock's security pass at equal or better depth. `shared.md` reproduces the bar, the
do-not-report list, and the severity rubric near-verbatim.

**Stock prompt tactics still missing from adversarial** (port candidates):
- `git show <base_sha>:path` base-vs-head mechanics on refactors.
- Library/framework contract verification ("confirm the contract before assuming a bug or assuming
  safety").
- New-dependency pass with the lockfile nuance (don't report a missing manifest bound when the
  lockfile pins the resolved build).
- Dependency-install policy.
- The three pre-publish checks from §4 (zero-findings re-walk, same-file independence, title /
  top-directories cross-check).
- Closing-summary handling of `error: "thread_not_found"` (tool still returns it; adversarial's
  prompt doesn't say what to do) and the `surfaced_count` citation.

**Adversarial-only structural tactics** (stock lacks; keep):
- The adjudication verdict ladder with calibration: *kill requires constructible proof* (quote the
  disproving line, the guard, the invariant); default to keep-plausible for realistic-but-uncertain
  triggers (races, nil on rare-but-reachable paths, falsy zero, boundary off-by-ones, lost regex
  anchors); plausible is triage material the parent must concretely confirm before publishing, and
  dropped plausibles are mentioned in the final message rather than published.
- Recall-mode finders: pass through every candidate with a nameable failure scenario, never
  silently drop half-believed ones, never pad; precision is the adjudicator's job.
- `RESERVED_SUBAGENT_TOOLS`: definitions that give a subagent `add_finding` / `publish_review` /
  etc. fail validation, and a toolless `general-purpose` override is appended so deepagents'
  auto-added default can't inherit parent tools past the capability ceiling (prompt additionally
  says never dispatch it).
- Boot-time definition validation: malformed frontmatter, unknown tools, duplicate tools, missing
  bodies all fail process start with aggregated errors, and the parent template is render-smoke-
  tested at import so a bad `{placeholder}` KeyErrors at boot rather than at the first review.
- `shared.md`'s blanket untrusted-content rule covers PR title, body, *diff content, and code
  comments* — broader than stock's targeted wrappers (stock wraps title/body/threads/replies/trace
  but never states that diff content and code comments themselves are untrusted).

**Injection defenses otherwise shared:** XML data-block wrapping with whitespace-tolerant
closing-tag neutralization (`_escape_for_data_block`), GitHub-login grammar validation for author
attributes, 4000-char comment truncation (the last two only exercised on stock, since only stock
renders thread blocks).

## 7. Discrepancies discovered (disposition updated, second pass)

1. **Stale workflow line in the shared first-review message — filed as OSWE-84** (sub-issue of
   OSWE-33, P3, `kind:bug area:review-tooling source:code-review`). `_build_first_review_context`
   (`agent/reviewer.py:603` @ `af768adca`) says "Review using the ordered passes (mechanical grep →
   diff-line audit → security/auth if applicable → pipeline sweep → deep flow)" — names that match
   *neither* current prompt. For stock it's stale pass-naming; for adversarial it actively
   contradicts the orchestration (it tells the parent to review directly when finders should). Both
   graphs emit it on every first review, and it compounds with the OSWE-82 unconditional profile
   append (§10): two independent direct-review instructions now reach the adversarial parent.
2. **Production routing still points at stock — verified, see §11.** All four GitHub-webhook
   dispatch sites hardcode `assistant_id="reviewer"`
   (`agent/webhooks/github.py:191,312,620,863`) on main, on the `prod` branch, and on the active
   studio2 release; `reviewer_adversarial` is reachable only via `REVIEWER_ASSISTANT_ID` in the
   eval harness or direct API dispatch. The v1 prep guard means a naive flip would hard-fail every
   re-review and finding-reply run; routing must split by event type until v2 grows those flows.
3. **Stock's subagent persona is likely dropped at runtime — filed as OSWE-85** (sub-issue of
   OSWE-33, P3, adds `risk:operator-misled`). The Open SWE harness profile
   (`agent/prompt.py:403`) replaces declarative subagent system prompts with
   `OPEN_SWE_SHARED_BASE`; that's exactly why the definition loader carries file-based subagent
   prompts via a model-call middleware (`agent/utils/agent_definitions.py:143`, and
   `tests/agent_definitions/test_subagent_factory.py` exercises the survival path — the 2026-07-21
   handoff confirms that survival is integration-tested *for the file-based subagents only*).
   Stock's `_reviewer_subagent` (`agent/reviewer.py:368`) still passes a plain `system_prompt` — so
   its focused-review persona is plausibly being supplanted by the generic base in production. If
   confirmed, it also skews the OSWE-33 eval baseline: "stock" was measured without its subagent
   persona.

## 8. Deliberately omitted context inputs (the v1 adoption menu)

Each is stock-only today, listed with what it buys:

1. **Existing PR review threads block + reconciliation** — comment-awareness: fetches all inline
   threads (GraphQL, paginated, capped 100×20), reconciles tracked findings against live thread
   state (marker-matched; resolved/outdated threads auto-resolve findings; human replies recorded
   with a reassess flag), renders the XML block, and the prompt suppresses overlapping candidates.
   The single biggest duplicate-filing guard; prerequisite for production parity.
2. **Re-review + watch flow** — delta diff since `last_reviewed_sha`, existing-findings block,
   resolve/unchanged/changed triage per finding.
3. **Finding-reply flow** — reassess exactly one finding when a human replies, reply wrapped as
   untrusted data.
4. **AGENTS.md conventions** — root + scoped files fetched from the *base* ref, filtered to changed
   paths, injected as a mandatory-rules pass with nesting precedence.
5. **Org-wide review guidelines** — admin-set, cached 5 min.
6. **Repo style prompt** — the analyzer-learned per-repo style appendix. Adversarial currently
   feeds outcomes into the learning loop without consuming its output.
7. **API-standards skill** — conditional best-practices pass when the diff touches API surfaces.
8. **PR trace context** — resolves the LangSmith thread that generated the PR (branch, then
   head-sha fallback), dumps raw runs to a sandbox JSON, prompt says grep it rather than load it,
   treat as untrusted, never publish trace content.
9. **Repo reviewer skills** — trusted-ref skill extraction + `SkillsMiddleware` listing.
10. **Diff grouping background pass** — fire-and-forget structured-output pass storing
    `diff_groups` for the dashboard's AI-sorted view; adversarial-run threads render without it.

## 9. Suggested sequencing (menu — only §7's discrepancies are filed; the rest awaits Eric's re-review)

This is a menu, not a plan; most items may be discarded. Rough grouping by leverage:

- **Before any production default flip:** §8.1 threads/reconciliation; routing split or v2
  re-review + finding-reply flows; OSWE-84 (stale line) + OSWE-82 (profile append) so the
  adversarial prompt is trusted again; OSWE-79 spine so pass counts are structural.
- **Likely precision lift, cheap:** §8.4 AGENTS.md, §8.6 style consumption, §8.5 org guidelines
  (all string-append inputs to the parent prompt render).
- **Prompt ports into finders/parent:** the §6 "still missing" list — but sequence against OSWE-79:
  the spine's structured candidate schema will subsume parts of the finder prose contract, so port
  tactics into the persona bodies, not into prose the spine replaces.
- **Parity oddments:** §8.10 grouping pass; OSWE-85 stock-subagent verification (benefits stock and
  the eval baseline regardless of the flip).

---

## 10. Addendum (second pass): OSWE-76 stage profiles changed both reviewers

Merged after §1–§9 were drafted (`af768adca`, "stage profiles — agent behavior as versioned
data"). Mechanics, then the delta per graph.

**The mechanism** (`agent/utils/stage_profiles.py`, `agent/profiles/<stage>/<name>.md`): a stage
profile is a frontmatter-validated markdown file whose body becomes the stage's prompt template.
Frontmatter may pin `model` + `reasoning_effort` (validated against `SUPPORTED_MODEL_IDS`, must be
set together) and may *restrict* `tools` (validated against the stage's curated ceiling —
`REVIEW_STAGE_TOOL_NAMES` = the 9 deep-agent builtins + the 10 reviewer tools; profiles can only
narrow, never add). Template fields are whitelisted per stage (review:
`working_dir/repo_owner/repo_name/pr_number/review_finding_cap/repo_checkout_note/historical_review_guidance`)
and render-smoke-tested at load. Selection comes from team settings (`review_profile`, cached
60 s); resolution falls back selected → `default` → the built-in `REVIEWER_PROMPT_TEMPLATE`
constant, logging instead of aborting graph construction. `agent/profiles/review/default.md` is the
stock reviewer template as versioned data (byte-equivalent content, `{}` frontmatter). The prep
fingerprint now includes the profile name, so switching profiles re-prepares checkpointed runs.
When a profile restricts tools, `ExcludeToolsMiddleware(allowed=…)` is appended to the parent
middleware stack *and* to every subagent's middleware (both graphs).

**Stock reviewer delta:** `_reviewer_system_prompt` renders `profile_body` in place of the
hardcoded template; profile model/effort override team defaults (but not explicit per-run config
keys). Behavior with the default profile is byte-identical to before — proven by identity tests for
the plan and stock-review assemblies.

**Adversarial delta — this is OSWE-82:** `PrepareAdversarialReviewerRunMiddleware._prepare` now
appends the *full rendered profile prompt* after the definition render unconditionally
(`agent/reviewer_adversarial.py:253-273`): `system_prompt = definition + "\n\n" +
_reviewer_system_prompt(profile_body=…)`. With the default profile that is the entire 208-line
stock procedure — "delegate at most one review pass", the nine-pass workflow — appended to a prompt
whose whole point is fan-out/adjudicate orchestration. OSWE-82 (Triage, P0-marked-high, filed from
the operator's post-merge review) carries the fix spec: append only when a non-default profile is
selected, default = definition alone byte-identical to pre-OSWE-76, plus the missing
adversarial-assembly identity test. (Amended per the PR #29 review: "append only non-default"
still collides — a review `StageProfile.body` is a *complete* stage prompt the stock reviewer
renders in place, so appending any full body onto the definition yields two instruction sets. The
clean semantics: the adversarial graph applies a profile's model/effort/tools pins but never
appends its body; if the adversarial prompt ever needs profile-tuning, that is the dedicated
adversarial stage OSWE-82 already names as the deferred alternative. Flagged on OSWE-82.) The eval-suffix handling also moved inside
`_reviewer_system_prompt`, so the adversarial graph now inherits it through the appended profile
prompt rather than appending `REVIEWER_EVAL_PROMPT_SUFFIX` itself — the OSWE-82 fix must preserve
eval-mode calibration when it stops appending the profile body.

Also new in OSWE-76 and relevant to this catalog: a second frontmatter parser now exists
(`stage_profiles.py` beside `agent_definitions.py`) and a `DEEP_AGENT_TOOL_NAMES` constant that can
drift from deepagents' actual builtin set — both already tracked as OSWE-83.

## 11. Deployment routing — verified (2026-07-22)

**Question:** does production route PR reviews to the adversarial graph anywhere? **Answer: no.**

- **Code:** all four webhook dispatch sites pass `assistant_id="reviewer"`; a non-UUID
  `assistant_id` resolves to the graph name in LangGraph, and `langgraph.json` maps `reviewer` →
  `agent.graphs.reviewer:traced_reviewer_agent` (stock). True on `main` (`af768adca`), on `prod`
  (`d3db1df18`), and unchanged by OSWE-76 (its diff doesn't touch `agent/webhooks/`).
- **Branches:** `prod` is a daily 08:00 UTC force-push mirror of `main`
  (`.github/workflows/promote_main_to_prod.yml`); at verification time prod sat one commit behind
  (`d3db1df18` vs `af768adca`).
- **studio2:** deployment is SHA-pinned via `studio2-ops` `release-activate` (symlink flip under
  `/opt/mobilyze/open-swe-control-plane`), independent of the `prod` branch. The active release is
  `af768adc` per OSWE-82's operator evidence ("Deployed defect (release af768adc)", filed
  2026-07-22); the 2026-07-21 handoff confirms the mechanism and that no webhook routing change has
  ever shipped. Not verified by touching the host — corroborated three ways instead.
- **Dispatch-site → event map** (for the eventual flip; corrected per the PR #29 review — sites
  are not one-to-one with dispatch kinds):
  - `trigger_pr_review_from_ref` (`github.py:191`) — explicit review request. Note (adjudicated
    PR #29 finding, upheld in OSWE-92's corrected record): this site never reads
    `last_reviewed_sha` and never sets `re_review` — an explicit re-request on a reviewed PR
    dispatches with first-review semantics today. OSWE-87 decides whether to fix the site or route
    on thread metadata.
  - `_dispatch_first_review_from_pr_payload` (`github.py:312`) — `opened`/`ready_for_review`, BUT
    it computes `is_re_review = bool(last_reviewed_sha)` and dispatches `re_review=True` when the
    canonical thread was reviewed before (draft→ready, reopen). Routing must key on the *computed
    dispatch kind* (`re_review`/`reviewer_event` in the assembled configurable), not on the
    webhook handler.
  - `process_github_push_event` (`github.py:620`) — re-review on push.
  - `process_github_review_finding_reply` (`github.py:863`) — finding-reply reassessment.
- **Flip design note:** findings, watch state, `last_reviewed_sha`, and check-run ids all live on
  thread metadata and are read through shared tools, so the reviewer thread is graph-agnostic — a
  per-event split (first reviews → `reviewer_adversarial`, push/reply events → stock) is
  mechanically sound as a transition, because a stock re-review can pick up findings an adversarial
  first review recorded. The end-state flip is a 4-site change (or a team-setting-driven selector in
  the same shape as `review_profile`), gated on v2 re-review + finding-reply flows landing in the
  adversarial graph (the v1 prep guard hard-fails those dispatches today).

## 12. v2 scoping map — what exists where (answers "is v2 scoped elsewhere?")

**Already scoped in Linear (do not re-file):**

- **OSWE-79** (Triage, P4, sub-issue of OSWE-33) — deterministic orchestration spine: five-stage
  StateGraph (prepare → find → dedupe → adjudicate → record+publish), structured candidate schema
  with IDs, verdicts keyed to IDs, publish structurally unreachable until adjudication completes,
  ~150–250 LOC budget. Fully specified with acceptance criteria; the 2026-07-21 session preferred
  landing it before further topology experiments.
- **OSWE-82** (Triage, high) — the §10 prompt regression, fix spec included.
- **OSWE-83** (Triage) — OSWE-76 cleanup: shared frontmatter parser, `DEEP_AGENT_TOOL_NAMES`
  de-drift, single-source default prompts.
- **OSWE-73 / OSWE-74** (Backlog) — upstream ladder (prompt-as-markdown to open-swe; declarative
  SubAgent loader to deepagents), each gated on a maintainer-caliber adversarial pass.
- **OSWE-75** (Cancelled) — adjudicator topology experiment as a standalone ticket; superseded by
  the completed 2026-07-22 A/B run (50 goldens, arm B directionally ahead — F1 0.287 vs 0.265 —
  but p≈0.45; recommendation "keep arm B" awaiting Eric's adopt/reject on OSWE-33).
- **Filed this session:** OSWE-84 (§7.1), OSWE-85 (§7.3); evidence comment added to OSWE-82.

**NOT scoped anywhere before this doc — this doc is the scoping artifact for it:**

- The capability-parity menu (§8): threads/reconciliation, re-review + watch, finding-reply,
  AGENTS.md, org guidelines, repo style consumption, API-standards skill, trace context, repo
  skills, diff grouping. The 2026-07-21 handoff explicitly deferred these "only if the graph is
  ever promoted beyond the experiment" — the default-adversarial goal triggers that condition now.
- The prompt-tactic ports (§6 "still missing" list).
- The routing flip itself (§11 flip design note) and its per-event transition plan.

Eric's stated intent (2026-07-22): default-adversarial once v2 completes. Next step per Eric:
re-review this doc against code, then commit to a plan and file the chosen items (likely as
OSWE-33 sub-issues or a new v2 umbrella).

## 13. Mechanical details swept up on the second pass (so the re-review misses nothing)

- **Confidence never gates.** Every finding records `confidence`, but publication filters on
  severity only; confidence exists for post-hoc calibration (`agent/review/findings.py:97-99`).
  Adversarial personas suggest severity but the prompt never tells the parent how to set
  confidence — a free calibration lever.
- **File-level findings** (both lines `None`) are accepted by `add_finding` but never render as
  inline comments (`render_inline_comment_payload` returns `None`); they count toward hidden
  totals only.
- **Eval-mode publish overrides:** `reviewer_eval_severity_threshold` and `reviewer_eval_cap` in
  configurable override the threshold/cap for the dry-run path; the simulated publication lands in
  thread metadata under `reviewer_eval_publication` for the judge to read.
- **Sub-threshold surfacing:** the review body adds "N additional finding(s) can be viewed in the
  web app" for open in-diff findings below the threshold — the agent controls threshold choice at
  `publish_review(severity_threshold=…)` (default `medium`).
- **Grouping model chain** (stock-only feature): `grouping_model_id` per-run override → team
  grouping default → inherits the reviewer *subagent* team default.
- **`fetch_review_diff` caps** metadata at 200 files (`files_truncated` flag); the diff file itself
  is uncapped.
- **Duplicate-summary detection** (`open_swe_review_exists`) walks paginated reviews looking for
  the `<!-- open-swe-reviewer pr=N -->` marker and is deliberately tri-state; `None` (API failure)
  must never suppress (fail toward possible duplicate, not silence).
- **Neither reviewer graph** carries `SandboxCircuitBreakerMiddleware` or `ensure_no_empty_msg`
  (main-agent middleware); reviewer runs rely on `ModelCallLimitMiddleware(exit_behavior="end")`
  plus the settle hooks.
- **Experiment context for baselines:** the completed A/B compared adversarial arm A vs arm B on
  release `d3db1df18` with v2 prompts. Any future stock-vs-adversarial comparison should wait for
  OSWE-82 + OSWE-84 (adversarial prompt integrity) and OSWE-85 (stock persona integrity) — both
  sides of the current baseline carry a prompt-delivery defect.

## 14. Menu triage (2026-07-22, Eric asked "any we shouldn't do?")

Recommended **skips and defers** from the §8/§6 menu, with reasons; everything not listed here
stays recommended:

- **Don't port re-review or finding-reply INTO the adversarial graph.** Route those events to the
  stock reviewer instead — possibly permanently, not just as a transition. Finding-reply is a
  single-finding reassessment; dispatching a finder roster at one human comment is pure waste, and
  stock's scoped flow is already the right tool. Re-review deltas are small; the fan-out topology
  earns its cost on first full review. Because findings state is graph-agnostic (§11), the split is
  clean. This shrinks v2 materially: "default-adversarial" means first reviews + explicit requests,
  not adversarial-everywhere.
- **Skip PR trace context for v2.** Its stated purpose (reduce false positives using the generating
  thread's trace) overlaps the adjudicator, which kills false positives from the code itself; it
  only fires for agent-authored PRs; and it adds a rate-limited LangSmith search to prep. Revisit
  only if FP rate on agent-authored PRs stays high after OSWE-82/84 land.
- **Defer repo reviewer skills** until a target repo actually ships `.agents/skills` /
  `.claude/skills` reviewer content — otherwise it's dead-code parity.
- **Defer the API-standards skill append** to the same batch as org-guidelines/style (it's the
  same string-append shape); it's conditional bloat until API-heavy PRs are common in enrolled
  repos.
- **Don't double-implement the pre-publish checks.** The zero-findings re-walk / same-file
  independence / title-vs-directories cross-check belong in the OSWE-79 spine as code (or parent
  prompt if the spine is far) — pick one home, not both.
- **Don't DRY the bar/severity prompt wording.** `shared.md`'s phrasing deliberately diverged from
  the stock template in the v2 prompt tuning (recall-mode framing); re-coupling the wording would
  undo OSWE-33's deliberate decoupling. The duplication to kill is code/data copies (§15), not
  tuned prose.
- **Watch-out on all context appends (AGENTS.md, org guidelines, style):** in stock, parent =
  reviewer, so appending to the parent prompt works. In the adversarial topology the parent doesn't
  review — conventions/style must reach the *finders* (and the adjudicator for judging), and
  appending AGENTS.md to every finder multiplies its token cost by roster size. Design options: a
  dedicated conventions finder persona, or selective distribution in the task messages. The prep
  unification (§15.1) should produce a context bundle the graph can route, not a pre-baked parent
  prompt.

## 15. DRY / upstream-sync opportunities (2026-07-22)

Ranked by leverage; `agent/reviewer.py` is the highest-churn upstream file (PRs #1690–#1765), so
the sync strategy is few extraction points with tiny call-site diffs, never a parallel copy that
must mirror upstream by hand.

1. **Unify reviewer run prep.** `PrepareAdversarialReviewerRunMiddleware._prepare` (~130 lines)
   hand-copies the stock prep's sandbox/repo/diff/metadata/first-review-context sequence and will
   drift on every upstream prep change. Extract the context-gathering into shared helpers (e.g.
   `gather_review_context(...) -> bundle` + a prompt-renderer parameter); the adversarial subclass
   keeps only its guard and renderer, and each §8 context input becomes a bundle field both graphs
   can opt into. This one refactor delivers most of the menu as configuration AND removes the
   biggest divergence surface. Upstream merges then land in one place.
2. **Single-source the review prompt template.** `agent/profiles/review/default.md` is
   byte-identical (11,545 chars, verified 2026-07-22) to `REVIEWER_PROMPT_TEMPLATE`; the constant
   should read the packaged file (or vice versa). Already named in OSWE-83 ("single-source default
   prompts") — and OSWE-73 (upstream prompt-as-packaged-markdown) is the upstream-ladder version of
   the same move: if upstream accepts it, the fork's profile default aligns with an upstream file
   and the duplication dies at the source.
3. **Shared frontmatter parser + tool-name constants** — `stage_profiles.py` re-implements the
   `agent_definitions.py` frontmatter parse, and `DEEP_AGENT_TOOL_NAMES` can drift from deepagents'
   real builtin set. Filed as OSWE-83; nothing new to add.
4. **Webhook assistant selector.** Replace the four hardcoded `assistant_id="reviewer"` sites with
   one selector helper keyed on the *computed dispatch kind*, not the webhook event — per the
   PR #29 review, the `opened`/`ready_for_review` handler also dispatches re-reviews when
   `last_reviewed_sha` exists, so the selector signature is
   `reviewer_assistant_for_dispatch(*, re_review, finding_reply, explicit_request) -> str`
   (team-setting- or env-driven, same shape as `review_profile`), called after each site assembles
   its configurable. Makes the §11 flip a config change, keeps the four sites from drifting, and
   encodes the split (fresh first reviews / explicit requests → adversarial; any
   `re_review`/finding-reply dispatch → stock). Spec updated on OSWE-87.
5. **Shared eval-suffix handling.** OSWE-76 moved `REVIEWER_EVAL_PROMPT_SUFFIX` inside
   `_reviewer_system_prompt`; the OSWE-82 fix (stop appending the profile prompt by default) must
   re-attach eval calibration for the adversarial graph — do it by calling one shared append
   helper, not by re-inlining the suffix.
6. **Sync tripwires, not just sharing.** Extend the existing import-the-privates pattern with
   identity tests: the adversarial-assembly identity test OSWE-82 already specs, plus a test
   asserting `profiles/review/default.md` == the in-code template until #2 lands. Run the reviewer
   identity/test suite on every upstream merge — prompt drift is the failure mode upstream merges
   introduce silently.
7. **Explicitly not worth it now:** merging the stage-profile and agent-definition file conventions
   into one system (speculative architecture; anti-bloat bar), and deduplicating
   model-pair-resolution helpers beyond what #1 absorbs naturally.

## 16. Operator ruling and the filed v2 batch (2026-07-22, supersedes §14 where they differ)

Eric approved §14/§15 with two reversals: **trace context stays in** (accepted FP-overlap concern,
kept anyway) and **repo reviewer skills are not deferred**. Consequence applied: the API-standards
append rides along too — with the unified prep it's one optional bundle field, and deferring it
alone would manufacture a follow-up ticket for a one-line addition. The conventions-finder /
selective-distribution design was approved as the shape for finder-bound context.

**The filed batch — umbrella OSWE-86 (child of OSWE-1):**

| Wave | Issue(s) | Owns (files) | Concurrency |
| --- | --- | --- | --- |
| 0 | OSWE-82 → OSWE-84 (lane 1, sequential); OSWE-85 (lane 2); OSWE-83 (lane 3); OSWE-87 selector (lane 4) | adversarial prompt region / reviewer.py subagent + tests / parsers + profiles / webhooks | 4 parallel lanes, file-disjoint |
| 1 | OSWE-88 prep unification | reviewer.py + reviewer_adversarial.py | solo |
| 2 | OSWE-79 spine (+ pre-publish checks as code, P2) | reviewer_adversarial.py | solo |
| 3 | OSWE-89 context parity (threads/reconcile, org, style, API, trace, grouping) | bundle + adversarial stages | solo |
| 4 | OSWE-90 conventions finder + distribution + repo skills | definitions dir + find-stage wiring | solo |
| 5 | OSWE-91 flip + canary | config only | solo |
| any | OSWE-73 / OSWE-74 upstream-ladder prep | proposals/docs | free side lane |

Governing rule: `agent/reviewer_adversarial.py` is the rebase-thrash bottleneck — one in-flight
task owns it at a time. Peak useful concurrency is wave 0's four lanes (+ the upstream side lane);
after that the work is deliberately serial. Routing end-state: adversarial for first reviews +
explicit requests; push re-reviews and finding replies stay on stock permanently (OSWE-87's
selector interlock enforces this even under misconfig).

## 17. Merge escape hatch (OSWE-93, applied 2026-07-22)

Repo governance now splits `main` protection into two rulesets: **"Protect main"** (deletion,
non-fast-forward, PR-required, and the eight CI/lint/CodeQL required checks; `bypass_actors`
empty — CI is never bypassable) and **"Review gate"** (the single required "Open SWE Review"
check; bypass: organization admins, pull-request mode only). The hatch is
`gh pr merge <n> --squash --admin`, and it succeeds exactly when the only red rule is the review
gate. Protocol: the hatch fires **only on the operator's explicit merge instruction for that PR**
— never agent-initiated; skipped-over findings remain tracked in the findings ledger. GitHub
records every bypass in the merge event and ruleset insights. First live use: PR #29
(`88fc4135c`). Complementary to OSWE-92's settle-anywhere fix: 92 keeps the gate honest and
writable; the hatch covers "merge now regardless," by the operator alone.
