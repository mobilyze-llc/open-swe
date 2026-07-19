# Behavior Contract v1

`mobilyze.behavior-contract.v1` freezes user-observable acceptance clauses before
implementation starts. The accepted record binds the canonical contract hash to an owning issue or
approved plan and its approval event. Once implementation starts, changed content requires both a
higher `contract_version` and a new approval event whose subject hash matches that content.

The runtime has four fixed probe types: `cli`, `http_api`, `generated_artifact`, and `process`.
Probes consume named repository-approved fixtures and bounded public observations; they cannot carry
commands, code, templates, credential values, source files, diffs, history, tests, or traces. Reports
contain compact summaries and `probe://` references rather than raw output.

## Example contract

```json
{
  "schema": "mobilyze.behavior-contract.v1",
  "contract_version": 1,
  "owner": {
    "type": "issue",
    "reference": "OSWE-34"
  },
  "user_visible_goal": "An invalid CLI request is rejected deterministically.",
  "target": {
    "type": "cli",
    "reference": "open-swe"
  },
  "approved_fixtures": ["invalid-cli"],
  "credential_references": [],
  "clauses": [
    {
      "id": "reject-invalid-input",
      "task": "Run the approved invalid-input fixture.",
      "expected_behavior": "The CLI rejects the request with exit code 2.",
      "failure_behavior": "A zero exit or a different failure code fails validation.",
      "evidence_types": ["exit_code"],
      "probe": {
        "type": "cli",
        "fixture": "invalid-cli",
        "expected_exit_code": 2,
        "stdout_contains": [],
        "stderr_contains": [],
        "stdout_fields": [],
        "filesystem_effects": []
      },
      "anti_cheat": true,
      "out_of_scope_reason": null,
      "adjacent_clause_ids": []
    },
    {
      "id": "browser-flow",
      "task": "Exercise the browser flow.",
      "expected_behavior": "The browser flow is excluded from this contract.",
      "failure_behavior": "The excluded flow must never be reported as passing.",
      "evidence_types": [],
      "probe": null,
      "anti_cheat": false,
      "out_of_scope_reason": "Behavior Contract v1 has no browser probe.",
      "adjacent_clause_ids": []
    }
  ]
}
```

The task stores `canonical_hash(contract)` with the approval event by calling
`accept_contract`, then calls `start_implementation` before execution. `run_contract` accepts that
bound record rather than a bare contract, so unapproved or silently changed content cannot execute.

## Example report

```json
{
  "schema": "mobilyze.behavior-report.v1",
  "contract_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "contract_version": 1,
  "owner_reference": "OSWE-34",
  "target_artifact_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "profile_image_hash": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "executor_version": "mobilyze.behavior-probes.v1",
  "selected_clause_ids": ["reject-invalid-input", "browser-flow"],
  "results": [
    {
      "clause_id": "reject-invalid-input",
      "status": "pass",
      "evidence": [
        {
          "type": "exit_code",
          "reference": "probe://reject-invalid-input/exit_code",
          "summary": "exit=2; checks=1; failures=0"
        }
      ],
      "reproduction_reference": "probe://reject-invalid-input/reproduce",
      "blocker": null,
      "anti_cheat_passed": true,
      "cache_hit": false
    },
    {
      "clause_id": "browser-flow",
      "status": "out_of_scope",
      "evidence": [],
      "reproduction_reference": null,
      "blocker": null,
      "anti_cheat_passed": null,
      "cache_hit": false
    }
  ]
}
```

Every selected clause has one explicit status: `pass`, `fail`, `blocked`, or `out_of_scope`.
Clause-cache identity is the exact tuple of target or artifact hash, clause hash, executor version,
and profile or image hash. Targeted reruns select affected or previously failed/blocked clauses and
only their declared adjacent probes, preserving contract order.
