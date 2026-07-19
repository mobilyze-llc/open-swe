from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from agent.mobilyze.behavior.binding import (
    ApprovalEvent,
    ContractMutationError,
    accept_contract,
    amend_contract,
    start_implementation,
)
from agent.mobilyze.behavior.cache import ClauseCache
from agent.mobilyze.behavior.codec import canonical_hash, content_sha256, contract_from_dict
from agent.mobilyze.behavior.executors import (
    ArtifactObservation,
    CliObservation,
    FileObservation,
    HttpObservation,
    ProcessObservation,
)
from agent.mobilyze.behavior.models import (
    ArtifactProbe,
    BehaviorContract,
    CliProbe,
    ContractClause,
    ContractOwner,
    ContractValidationError,
    EvidenceType,
    FileEffect,
    FileState,
    HttpProbe,
    JsonFieldExpectation,
    JsonKind,
    OwnerType,
    ProcessProbe,
    TargetRef,
    TargetType,
)
from agent.mobilyze.behavior.report import ClauseStatus
from agent.mobilyze.behavior.rerun import targeted_clause_ids
from agent.mobilyze.behavior.runner import ExecutionContext, run_contract

TARGET_HASH = "1" * 64
PROFILE_HASH = "2" * 64


def _contract(*, version: int = 1) -> BehaviorContract:
    return BehaviorContract(
        schema="mobilyze.behavior-contract.v1",
        contract_version=version,
        owner=ContractOwner(OwnerType.ISSUE, "OSWE-34"),
        user_visible_goal="Operators can verify public behavior deterministically.",
        target=TargetRef(TargetType.CLI, "open-swe"),
        approved_fixtures=("valid-cli", "invalid-cli", "api-write", "artifact", "process"),
        credential_references=("test-api-token",),
        clauses=(
            ContractClause(
                id="cli-valid",
                task="Run the documented valid invocation.",
                expected_behavior="The invocation succeeds and writes the public result.",
                failure_behavior="A non-zero exit or missing result fails validation.",
                evidence_types=(EvidenceType.EXIT_CODE, EvidenceType.PUBLIC_OUTPUT),
                probe=CliProbe(
                    fixture="valid-cli",
                    expected_exit_code=0,
                    stdout_contains=("created",),
                    stdout_fields=(JsonFieldExpectation(("ok",), JsonKind.BOOLEAN, expected=True),),
                    filesystem_effects=(
                        FileEffect("outputs/result.json", FileState.EXISTS, "3" * 64),
                    ),
                ),
                adjacent_clause_ids=("cli-invalid",),
            ),
            ContractClause(
                id="cli-invalid",
                task="Run the documented invalid-input fixture.",
                expected_behavior="The invocation rejects invalid input.",
                failure_behavior="Accepting invalid input fails validation.",
                evidence_types=(EvidenceType.EXIT_CODE,),
                probe=CliProbe(fixture="invalid-cli", expected_exit_code=2),
                anti_cheat=True,
            ),
            ContractClause(
                id="future-ui",
                task="Exercise the future browser surface.",
                expected_behavior="The browser surface is excluded from this version.",
                failure_behavior="The excluded surface must not be treated as passing.",
                evidence_types=(),
                out_of_scope_reason="The v1 contract has no browser probe.",
            ),
        ),
    )


def _binding(contract: BehaviorContract | None = None):
    accepted_contract = contract or _contract()
    approval = ApprovalEvent(
        event_id="linear-comment:approval-1",
        approved_by="operator:eric",
        owner=accepted_contract.owner,
        contract_version=accepted_contract.contract_version,
        contract_hash=canonical_hash(accepted_contract),
    )
    return start_implementation(
        accept_contract(accepted_contract, approval),
        event_id="linear-state:implementation-started",
    )


def _context(*, target_hash: str = TARGET_HASH, profile_hash: str = PROFILE_HASH):
    return ExecutionContext(
        target_artifact_hash=target_hash,
        profile_image_hash=profile_hash,
    )


def test_contract_is_immutable_and_hashes_equivalent_content_identically():
    contract = _contract()
    equivalent = contract_from_dict(
        {
            "target": {"reference": "open-swe", "type": "cli"},
            "credential_references": ["test-api-token"],
            "approved_fixtures": [
                "valid-cli",
                "invalid-cli",
                "api-write",
                "artifact",
                "process",
            ],
            "schema": "mobilyze.behavior-contract.v1",
            "contract_version": 1,
            "owner": {"type": "issue", "reference": "OSWE-34"},
            "user_visible_goal": "Operators can verify public behavior deterministically.",
            "clauses": [
                {
                    "id": "cli-valid",
                    "task": "Run the documented valid invocation.",
                    "expected_behavior": "The invocation succeeds and writes the public result.",
                    "failure_behavior": "A non-zero exit or missing result fails validation.",
                    "evidence_types": ["exit_code", "public_output"],
                    "anti_cheat": False,
                    "out_of_scope_reason": None,
                    "adjacent_clause_ids": ["cli-invalid"],
                    "probe": {
                        "type": "cli",
                        "fixture": "valid-cli",
                        "expected_exit_code": 0,
                        "stdout_contains": ["created"],
                        "stderr_contains": [],
                        "stdout_fields": [
                            {
                                "path": ["ok"],
                                "kind": "boolean",
                                "compare_value": True,
                                "expected": True,
                            }
                        ],
                        "filesystem_effects": [
                            {
                                "path": "outputs/result.json",
                                "state": "exists",
                                "sha256": "3" * 64,
                            }
                        ],
                    },
                },
                {
                    "id": "cli-invalid",
                    "task": "Run the documented invalid-input fixture.",
                    "expected_behavior": "The invocation rejects invalid input.",
                    "failure_behavior": "Accepting invalid input fails validation.",
                    "evidence_types": ["exit_code"],
                    "anti_cheat": True,
                    "out_of_scope_reason": None,
                    "adjacent_clause_ids": [],
                    "probe": {
                        "type": "cli",
                        "fixture": "invalid-cli",
                        "expected_exit_code": 2,
                        "stdout_contains": [],
                        "stderr_contains": [],
                        "stdout_fields": [],
                        "filesystem_effects": [],
                    },
                },
                {
                    "id": "future-ui",
                    "task": "Exercise the future browser surface.",
                    "expected_behavior": "The browser surface is excluded from this version.",
                    "failure_behavior": "The excluded surface must not be treated as passing.",
                    "evidence_types": [],
                    "anti_cheat": False,
                    "out_of_scope_reason": "The v1 contract has no browser probe.",
                    "adjacent_clause_ids": [],
                    "probe": None,
                },
            ],
        }
    )

    assert canonical_hash(contract) == canonical_hash(equivalent)
    assert canonical_hash(contract) != canonical_hash(replace(contract, contract_version=2))
    with pytest.raises(FrozenInstanceError):
        contract.user_visible_goal = "changed"  # type: ignore[misc]
    with pytest.raises(ContractValidationError, match="immutable tuple"):
        CliProbe(
            fixture="invalid-cli",
            expected_exit_code=2,
            stdout_contains=["rejected"],  # type: ignore[arg-type]
        )


def test_contract_parser_rejects_arbitrary_commands_and_credential_values():
    base = {
        "schema": "mobilyze.behavior-contract.v1",
        "contract_version": 1,
        "owner": {"type": "issue", "reference": "OSWE-34"},
        "user_visible_goal": "Reject unsafe contract content.",
        "target": {"type": "cli", "reference": "open-swe"},
        "approved_fixtures": ["fixture"],
        "credential_references": [],
        "clauses": [],
    }
    with pytest.raises(ContractValidationError, match="unknown fields: command"):
        contract_from_dict({**base, "command": "rm -rf /"})
    with pytest.raises(ContractValidationError, match="unknown fields: credential_values"):
        contract_from_dict({**base, "credential_values": ["secret"]})


def test_mutation_after_start_requires_new_version_and_explicit_approval():
    binding = _binding()
    changed = replace(binding.contract, user_visible_goal="A changed goal.")

    with pytest.raises(ContractMutationError, match="increase contract_version"):
        amend_contract(binding, changed, None)

    versioned = replace(changed, contract_version=2)
    with pytest.raises(ContractMutationError, match="approval event"):
        amend_contract(binding, versioned, None)

    approval = ApprovalEvent(
        event_id="linear-comment:approval-2",
        approved_by="operator:eric",
        owner=versioned.owner,
        contract_version=2,
        contract_hash=canonical_hash(versioned),
    )
    amended = amend_contract(binding, versioned, approval)
    assert amended.contract_hash == canonical_hash(versioned)
    assert amended.implementation_started_event == binding.implementation_started_event


def test_cli_and_invalid_input_probes_finish_explicitly():
    binding = _binding()
    observations = {
        "cli-valid": CliObservation(
            exit_code=0,
            stdout='{"ok": true, "message": "created"}',
            stderr="",
            filesystem=(FileObservation("outputs/result.json", exists=True, sha256="3" * 64),),
        ),
        "cli-invalid": CliObservation(exit_code=2, stdout="", stderr="invalid"),
    }

    report = run_contract(binding, observations, _context())

    assert [result.status for result in report.results] == [
        ClauseStatus.PASS,
        ClauseStatus.PASS,
        ClauseStatus.OUT_OF_SCOPE,
    ]
    assert report.results[1].anti_cheat_passed is True


def test_missing_observation_blocks_instead_of_passing():
    report = run_contract(_binding(), {}, _context(), clause_ids=("cli-valid",))

    assert report.results[0].status is ClauseStatus.BLOCKED
    assert report.results[0].blocker == "required observation was not supplied"


def test_http_response_and_persistence_assertions_are_deterministic():
    clause = ContractClause(
        id="api-persists",
        task="Create an item through the documented API fixture.",
        expected_behavior="The response and subsequent state expose the same item.",
        failure_behavior="A mismatched response or state fails validation.",
        evidence_types=(EvidenceType.HTTP_RESPONSE, EvidenceType.PERSISTENCE),
        probe=HttpProbe(
            fixture="api-write",
            expected_status=201,
            response_fields=(
                JsonFieldExpectation(("item", "id"), JsonKind.STRING, expected="item-1"),
            ),
            persistence_fields=(
                JsonFieldExpectation(("items", "item-1", "active"), JsonKind.BOOLEAN, True),
            ),
        ),
        anti_cheat=True,
    )
    contract = replace(_contract(), clauses=(clause,))
    report = run_contract(
        _binding(contract),
        {
            clause.id: HttpObservation(
                status_code=201,
                response={"item": {"id": "item-1"}},
                persisted_state={"items": {"item-1": {"active": True}}},
            )
        },
        _context(),
    )

    assert report.results[0].status is ClauseStatus.PASS
    assert report.results[0].anti_cheat_passed is True


def test_generated_artifact_hash_schema_and_content_are_checked():
    content = '{"schema":"result.v1","ok":true}'
    clause = ContractClause(
        id="artifact",
        task="Inspect the generated result artifact.",
        expected_behavior="The artifact has the approved content, schema, and hash.",
        failure_behavior="Any artifact mismatch fails validation.",
        evidence_types=(EvidenceType.ARTIFACT_HASH, EvidenceType.ARTIFACT_CONTENT),
        probe=ArtifactProbe(
            fixture="artifact",
            path="artifacts/result.json",
            expected_sha256=content_sha256(content),
            contains=('"schema":"result.v1"',),
            fields=(JsonFieldExpectation(("ok",), JsonKind.BOOLEAN, True),),
        ),
    )
    contract = replace(_contract(), clauses=(clause,))

    report = run_contract(
        _binding(contract),
        {clause.id: ArtifactObservation("artifacts/result.json", content)},
        _context(),
    )

    assert report.results[0].status is ClauseStatus.PASS


def test_process_launch_termination_and_public_log_are_checked_without_log_leakage():
    clause = ContractClause(
        id="process",
        task="Observe the documented process lifecycle fixture.",
        expected_behavior="The process launches, terminates, and emits the public marker.",
        failure_behavior="Missing lifecycle evidence fails validation.",
        evidence_types=(EvidenceType.PROCESS_LIFECYCLE, EvidenceType.PUBLIC_LOG),
        probe=ProcessProbe(
            fixture="process",
            expected_launched=True,
            expected_terminated=True,
            public_log_contains=("ready",),
        ),
    )
    contract = replace(_contract(), clauses=(clause,))
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"

    report = run_contract(
        _binding(contract),
        {
            clause.id: ProcessObservation(
                launched=True,
                terminated=True,
                exit_code=0,
                public_log=f"ready token={secret}",
            )
        },
        _context(),
    )

    assert report.results[0].status is ClauseStatus.PASS
    assert secret not in report.to_json()
    assert "token=" not in report.to_json()


def test_path_escape_and_source_inputs_fail_closed():
    with pytest.raises(ContractValidationError, match="safe relative artifact path"):
        ArtifactProbe(fixture="artifact", path="../../agent/server.py")
    with pytest.raises(ContractValidationError, match="source, test, diff, history, or trace"):
        ArtifactProbe(fixture="artifact", path="agent/server.py")


def test_cache_identity_is_exact_across_all_four_components():
    binding = _binding()
    observations = {
        "cli-invalid": CliObservation(exit_code=2, stdout="", stderr="invalid"),
    }
    cache = ClauseCache()

    first = run_contract(
        binding,
        observations,
        _context(),
        cache=cache,
        clause_ids=("cli-invalid",),
    )
    second = run_contract(
        binding,
        observations,
        _context(),
        cache=cache,
        clause_ids=("cli-invalid",),
    )
    changed_target = run_contract(
        binding,
        observations,
        _context(target_hash="4" * 64),
        cache=cache,
        clause_ids=("cli-invalid",),
    )
    changed_profile = run_contract(
        binding,
        observations,
        _context(profile_hash="5" * 64),
        cache=cache,
        clause_ids=("cli-invalid",),
    )
    changed_executor = run_contract(
        binding,
        observations,
        replace(_context(), executor_version="mobilyze.behavior-probes.v2"),
        cache=cache,
        clause_ids=("cli-invalid",),
    )
    changed_clause = replace(
        binding.contract.clauses[1],
        expected_behavior="Invalid input is rejected with the documented exit status.",
    )
    changed_contract = replace(binding.contract, contract_version=2, clauses=(changed_clause,))
    changed_clause_binding = _binding(changed_contract)
    changed_clause_report = run_contract(
        changed_clause_binding,
        observations,
        _context(),
        cache=cache,
    )

    assert first.results[0].cache_hit is False
    assert second.results[0].cache_hit is True
    assert changed_target.results[0].cache_hit is False
    assert changed_profile.results[0].cache_hit is False
    assert changed_executor.results[0].cache_hit is False
    assert changed_clause_report.results[0].cache_hit is False


def test_targeted_rerun_includes_only_affected_failed_and_declared_adjacent_clauses():
    contract = _contract()
    prior = run_contract(
        _binding(contract),
        {
            "cli-valid": CliObservation(exit_code=1, stdout="", stderr="failed"),
            "cli-invalid": CliObservation(exit_code=2, stdout="", stderr="invalid"),
        },
        _context(),
    )

    selected = targeted_clause_ids(contract, prior_report=prior)

    assert selected == ("cli-valid", "cli-invalid")
