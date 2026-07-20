from __future__ import annotations

import inspect
import json

import pytest
from pydantic import ValidationError

from agent.mobilyze.behavior import (
    MAX_RAW_EVIDENCE_LENGTH,
    ApprovalEvent,
    BehaviorContract,
    ClauseStatus,
    CliObservation,
    CliProbe,
    ContractIdentity,
    EvaluationReport,
    EvidenceIssueCode,
    EvidenceKind,
    FilesystemAssertion,
    FilesystemObservation,
    FilesystemProbe,
    FilesystemState,
    Fixture,
    GeneratedArtifactAssertion,
    GeneratedArtifactObservation,
    GeneratedArtifactProbe,
    HttpMethod,
    HttpObservation,
    HttpProbe,
    ObservableClause,
    ObservablePath,
    OutOfScopeClause,
    Presence,
    ProcessAssertion,
    ProcessObservation,
    ProcessProbe,
    ProcessState,
    RawEvidence,
    TargetAccessReference,
    bind_task,
    evaluate_contract,
    observation_from_persisted_dict,
    observation_from_persisted_json,
    start_implementation,
    transition_contract,
)

REVISION = "31385eeb92fbe1fc1511264bf9b380b92e26b305"


def _cli_fixture(name: str = "cli-help") -> Fixture:
    return Fixture(
        name=name,
        probe=CliProbe(
            executable_reference="approved-cli",
            expected_exit_code=0,
            stdout_contains="ready",
        ),
    )


def _clause(
    clause_id: str = "cli-works",
    fixture_name: str = "cli-help",
    evidence: tuple[EvidenceKind, ...] = (
        EvidenceKind.CLI_EXIT_STATUS,
        EvidenceKind.CLI_PUBLIC_OUTPUT,
    ),
) -> ObservableClause:
    return ObservableClause(
        clause_id=clause_id,
        statement="The approved CLI reports that it is ready.",
        fixture_name=fixture_name,
        success_behavior="The CLI is ready.",
        failure_behavior="The CLI is not ready.",
        required_evidence=evidence,
    )


def _contract(
    *,
    contract_id: str = "oswe-34-contract",
    version: int = 1,
    goal: str = "Expose the approved behavior.",
    fixtures: tuple[Fixture, ...] | None = None,
    clauses: tuple[ObservableClause, ...] | None = None,
    roots: tuple[ObservablePath, ...] = (),
    out_of_scope: tuple[OutOfScopeClause, ...] = (),
) -> BehaviorContract:
    return BehaviorContract(
        contract_id=contract_id,
        version=version,
        goal=goal,
        target=TargetAccessReference(target="mobilyze/open-swe", access_reference="github-app"),
        approved_credential_references=("github-app",),
        observable_output_roots=roots,
        fixtures=fixtures or (_cli_fixture(),),
        clauses=clauses or (_clause(),),
        out_of_scope_clauses=out_of_scope,
    )


def _approval(
    contract: BehaviorContract,
    *,
    task_id: str = "OSWE-34",
    revision: str = REVISION,
    event_id: str = "approval-1",
) -> ApprovalEvent:
    return ApprovalEvent(
        event_id=event_id,
        task_id=task_id,
        contract_identity=contract.identity,
        target_revision=revision,
        approved_by="product-owner",
    )


def _binding(contract: BehaviorContract, revision: str = REVISION):
    return bind_task(
        task_id="OSWE-34",
        contract=contract,
        target_revision=revision,
        approval=_approval(contract, revision=revision),
    )


def _cli_observation(
    contract: BehaviorContract,
    *,
    clause_id: str = "cli-works",
    fixture_name: str = "cli-help",
    exit_code: int = 0,
    stdout: str = "ready",
    revision: str = REVISION,
    task_id: str = "OSWE-34",
    identity: ContractIdentity | None = None,
    evidence: tuple[RawEvidence, ...] | None = None,
) -> CliObservation:
    return CliObservation(
        task_id=task_id,
        contract_identity=identity or contract.identity,
        target_revision=revision,
        clause_id=clause_id,
        fixture_name=fixture_name,
        exit_code=exit_code,
        public_stdout=stdout,
        evidence=evidence
        or (
            RawEvidence(kind=EvidenceKind.CLI_EXIT_STATUS, summary=f"exit={exit_code}"),
            RawEvidence(kind=EvidenceKind.CLI_PUBLIC_OUTPUT, summary=stdout),
        ),
    )


def test_contract_is_frozen_serializable_and_semantically_hashed() -> None:
    contract = _contract()
    restored = BehaviorContract.from_persisted_json(contract.to_persisted_json())

    assert restored == contract
    assert restored.canonical_json() == contract.canonical_json()
    assert len(contract.content_hash) == 64
    assert _contract(goal="Different user-visible goal.").content_hash != contract.content_hash
    assert _contract(contract_id="other-contract").content_hash != contract.content_hash
    assert _contract(version=2).content_hash == contract.content_hash
    assert _contract(version=2).identity != contract.identity

    with pytest.raises(ValidationError):
        contract.__setattr__("goal", "mutated")


def test_observation_and_report_round_trip_through_persisted_json() -> None:
    contract = _contract()
    binding = _binding(contract)
    observation = _cli_observation(contract)
    restored_observation = observation_from_persisted_json(observation.to_persisted_json())
    restored_dict_observation = observation_from_persisted_dict(observation.to_persisted_dict())
    report = evaluate_contract(contract, binding, [restored_observation])
    restored_report = EvaluationReport.from_persisted_json(report.to_persisted_json())

    assert restored_observation == observation
    assert restored_dict_observation == observation
    assert restored_report == report
    assert {status.value for status in ClauseStatus} == {
        "pass",
        "fail",
        "blocked",
        "out_of_scope",
    }


def test_contract_and_observations_reject_arbitrary_fields_and_unknown_probes() -> None:
    probe_payload = CliProbe(
        executable_reference="approved-cli", expected_exit_code=0
    ).to_persisted_dict()
    probe_payload["argv"] = ["--unsafe"]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CliProbe.from_persisted_dict(probe_payload)

    unknown_probe = _cli_fixture().to_persisted_dict()
    unknown_probe["probe"] = {"kind": "plugin", "code": "return True"}
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        Fixture.from_persisted_dict(unknown_probe)

    observation_payload = _cli_observation(_contract()).to_persisted_dict()
    observation_payload["command"] = "approved-cli --help"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        observation_from_persisted_json(json.dumps(observation_payload))


def test_observable_paths_are_typed_relative_and_fail_closed() -> None:
    cases = {
        ".": "observable_path_empty",
        "../artifact.json": "observable_path_escape",
        "agent//generated.json": "observable_path_malformed",
        "/tmp/artifact.json": "observable_path_absolute",
        "agent/.git/config": "observable_path_forbidden",
        "agent/nested/source/file.py": "observable_path_forbidden",
        "agent/nested/tests/result.json": "observable_path_forbidden",
        "agent/nested/diff/result.json": "observable_path_forbidden",
        "agent/nested/history/result.json": "observable_path_forbidden",
        "agent/nested/trace/result.json": "observable_path_forbidden",
    }
    for value, error_type in cases.items():
        with pytest.raises(ValidationError) as raised:
            ObservablePath(value)
        assert raised.value.errors()[0]["type"] == error_type

    root = ObservablePath("agent/generated")
    fixture = Fixture(
        name="manifest",
        probe=GeneratedArtifactProbe(
            path=ObservablePath("agent/generated/manifest.json"),
            assertion=GeneratedArtifactAssertion.EXISTS,
        ),
    )
    contract = _contract(
        fixtures=(fixture,),
        clauses=(
            ObservableClause(
                clause_id="manifest-exists",
                statement="The generated manifest exists.",
                fixture_name="manifest",
                success_behavior="The manifest exists.",
                failure_behavior="The manifest is absent.",
                required_evidence=(EvidenceKind.ARTIFACT_EXISTENCE,),
            ),
        ),
        roots=(root,),
    )
    probe = contract.fixtures[0].probe
    assert isinstance(probe, GeneratedArtifactProbe)
    assert str(probe.path) == "agent/generated/manifest.json"


def test_existence_only_probe_cannot_require_content_evidence() -> None:
    fixture = Fixture(
        name="manifest",
        probe=GeneratedArtifactProbe(
            path=ObservablePath("agent/generated/manifest.json"),
            assertion=GeneratedArtifactAssertion.EXISTS,
        ),
    )
    with pytest.raises(ValidationError, match="requires unsupported evidence"):
        _contract(
            fixtures=(fixture,),
            clauses=(
                ObservableClause(
                    clause_id="manifest-exists",
                    statement="The generated manifest exists.",
                    fixture_name="manifest",
                    success_behavior="The manifest exists.",
                    failure_behavior="The manifest is absent.",
                    required_evidence=(EvidenceKind.ARTIFACT_CONTENT,),
                ),
            ),
            roots=(ObservablePath("agent/generated"),),
        )


def test_every_approval_event_is_validated_even_for_unchanged_content() -> None:
    contract = _contract()
    binding = _binding(contract)
    invalid = _approval(contract, task_id="OTHER-1", event_id="invalid")

    with pytest.raises(ValueError, match="different task"):
        transition_contract(binding, contract, REVISION, invalid)
    with pytest.raises(ValueError, match="different task"):
        start_implementation(binding, invalid)


def test_changed_content_after_start_requires_next_version_and_new_approval() -> None:
    contract = _contract()
    binding = start_implementation(_binding(contract), _approval(contract, event_id="start"))
    changed_same_version = _contract(goal="Changed after start.")

    with pytest.raises(ValueError, match="requires the next version"):
        transition_contract(
            binding,
            changed_same_version,
            REVISION,
            _approval(changed_same_version, event_id="changed-same-version"),
        )

    changed_next_version = _contract(version=2, goal="Changed after start.")
    with pytest.raises(ValueError, match="new approval event"):
        transition_contract(
            binding,
            changed_next_version,
            REVISION,
            _approval(changed_next_version, event_id=binding.approval_event_id),
        )
    transitioned = transition_contract(
        binding,
        changed_next_version,
        REVISION,
        _approval(changed_next_version, event_id="changed-next-version"),
    )
    assert transitioned.contract_identity == changed_next_version.identity


def test_cross_contract_task_target_and_fixture_observations_cannot_satisfy_clause() -> None:
    contract = _contract(fixtures=(_cli_fixture(), _cli_fixture("anti-cheat")))
    binding = _binding(contract)
    other_contract = _contract(contract_id="other-contract")
    observations = [
        _cli_observation(contract, identity=other_contract.identity),
        _cli_observation(contract, task_id="OTHER-1"),
        _cli_observation(contract, revision="different-revision"),
        _cli_observation(contract, fixture_name="anti-cheat"),
    ]

    for observation in observations:
        result = evaluate_contract(contract, binding, [observation]).results[0]
        assert result.status is ClauseStatus.BLOCKED


def test_explicit_absence_is_distinct_from_missing_data() -> None:
    fixture = Fixture(
        name="no-debug-file",
        probe=FilesystemProbe(
            path=ObservablePath("output/debug.log"),
            assertion=FilesystemAssertion.ABSENT,
        ),
    )
    clause = ObservableClause(
        clause_id="debug-absent",
        statement="No debug log is generated.",
        fixture_name="no-debug-file",
        success_behavior="The debug log is absent.",
        failure_behavior="A debug log exists.",
        required_evidence=(EvidenceKind.FILESYSTEM_STATE,),
    )
    contract = _contract(fixtures=(fixture,), clauses=(clause,), roots=(ObservablePath("output"),))
    binding = _binding(contract)

    missing = evaluate_contract(contract, binding, []).results[0]
    observed_absence = evaluate_contract(
        contract,
        binding,
        [
            FilesystemObservation(
                task_id=binding.task_id,
                contract_identity=contract.identity,
                target_revision=binding.target_revision,
                clause_id=clause.clause_id,
                fixture_name=fixture.name,
                state=FilesystemState.ABSENT,
                evidence=(RawEvidence(kind=EvidenceKind.FILESYSTEM_STATE, summary="path absent"),),
            )
        ],
    ).results[0]

    assert missing.status is ClauseStatus.BLOCKED
    assert observed_absence.status is ClauseStatus.PASS


def test_contradictory_duplicates_cannot_flip_failure_to_pass() -> None:
    contract = _contract()
    binding = _binding(contract)
    failed = _cli_observation(contract, exit_code=1, stdout="not ready")
    passed = _cli_observation(contract)

    first = evaluate_contract(contract, binding, [failed, passed]).results[0]
    second = evaluate_contract(contract, binding, [passed, failed]).results[0]

    assert first == second
    assert first.status is ClauseStatus.FAIL
    assert not first.evidence
    assert any(issue.code.value == "duplicate_observation" for issue in first.issues)


def test_identical_duplicate_observations_are_blocked() -> None:
    contract = _contract()
    binding = _binding(contract)
    observation = _cli_observation(contract)

    result = evaluate_contract(contract, binding, [observation, observation]).results[0]

    assert result.status is ClauseStatus.BLOCKED
    assert any(issue.code.value == "duplicate_observation" for issue in result.issues)


def test_existence_only_observation_cannot_claim_content_evidence() -> None:
    fixture = Fixture(
        name="manifest",
        probe=GeneratedArtifactProbe(
            path=ObservablePath("agent/generated/manifest.json"),
            assertion=GeneratedArtifactAssertion.EXISTS,
        ),
    )
    clause = ObservableClause(
        clause_id="manifest-exists",
        statement="The generated manifest exists.",
        fixture_name="manifest",
        success_behavior="The manifest exists.",
        failure_behavior="The manifest is absent.",
        required_evidence=(EvidenceKind.ARTIFACT_EXISTENCE,),
    )
    contract = _contract(
        fixtures=(fixture,),
        clauses=(clause,),
        roots=(ObservablePath("agent/generated"),),
    )
    binding = _binding(contract)
    observation = GeneratedArtifactObservation(
        task_id=binding.task_id,
        contract_identity=contract.identity,
        target_revision=binding.target_revision,
        clause_id=clause.clause_id,
        fixture_name=fixture.name,
        presence=Presence.PRESENT,
        evidence=(
            RawEvidence(kind=EvidenceKind.ARTIFACT_EXISTENCE, summary="present"),
            RawEvidence(kind=EvidenceKind.ARTIFACT_CONTENT, summary="unapproved content claim"),
        ),
    )

    result = evaluate_contract(contract, binding, [observation]).results[0]

    assert result.status is ClauseStatus.BLOCKED
    assert EvidenceIssueCode.UNSUPPORTED in {issue.code for issue in result.evidence_issues}


def test_overlong_and_sensitive_evidence_have_distinct_classification_with_raw_bound_first() -> (
    None
):
    contract = _contract()
    binding = _binding(contract)
    overlong_secret = "token=super-secret " + "x" * MAX_RAW_EVIDENCE_LENGTH
    sensitive = "token=super-secret"
    basic_authorization = "Authorization: Basic dXNlcjpwYXNz"
    json_secret = '{"token":"super-secret-value"}'
    oauth_secret = '{"access_token":"secret-oauth-value"}'

    overlong = evaluate_contract(
        contract,
        binding,
        [
            _cli_observation(
                contract,
                evidence=(
                    RawEvidence(kind=EvidenceKind.CLI_EXIT_STATUS, summary=overlong_secret),
                    RawEvidence(kind=EvidenceKind.CLI_PUBLIC_OUTPUT, summary="ready"),
                ),
            )
        ],
    ).results[0]
    secret = evaluate_contract(
        contract,
        binding,
        [
            _cli_observation(
                contract,
                evidence=(
                    RawEvidence(kind=EvidenceKind.CLI_EXIT_STATUS, summary="exit=0"),
                    RawEvidence(kind=EvidenceKind.CLI_PUBLIC_OUTPUT, summary=sensitive),
                ),
            )
        ],
    ).results[0]

    assert overlong.status is ClauseStatus.BLOCKED
    assert overlong.evidence_issues[0].code is EvidenceIssueCode.OVERLONG
    assert overlong.evidence[0].summary == "<redacted:overlong>"
    basic = evaluate_contract(
        contract,
        binding,
        [
            _cli_observation(
                contract,
                evidence=(
                    RawEvidence(kind=EvidenceKind.CLI_EXIT_STATUS, summary="exit=0"),
                    RawEvidence(kind=EvidenceKind.CLI_PUBLIC_OUTPUT, summary=basic_authorization),
                ),
            )
        ],
    ).results[0]

    assert secret.status is ClauseStatus.BLOCKED
    assert secret.evidence_issues[0].code is EvidenceIssueCode.SENSITIVE
    assert secret.evidence[1].summary == "<redacted:sensitive>"
    assert "super-secret" not in secret.to_persisted_json()
    json_credential = evaluate_contract(
        contract,
        binding,
        [
            _cli_observation(
                contract,
                evidence=(
                    RawEvidence(kind=EvidenceKind.CLI_EXIT_STATUS, summary="exit=0"),
                    RawEvidence(kind=EvidenceKind.CLI_PUBLIC_OUTPUT, summary=json_secret),
                ),
            )
        ],
    ).results[0]

    assert basic.status is ClauseStatus.BLOCKED
    assert basic.evidence_issues[0].code is EvidenceIssueCode.SENSITIVE
    assert "dXNlcjpwYXNz" not in basic.to_persisted_json()
    oauth_credential = evaluate_contract(
        contract,
        binding,
        [
            _cli_observation(
                contract,
                evidence=(
                    RawEvidence(kind=EvidenceKind.CLI_EXIT_STATUS, summary="exit=0"),
                    RawEvidence(kind=EvidenceKind.CLI_PUBLIC_OUTPUT, summary=oauth_secret),
                ),
            )
        ],
    ).results[0]

    assert json_credential.status is ClauseStatus.BLOCKED
    assert json_credential.evidence_issues[0].code is EvidenceIssueCode.SENSITIVE
    assert "super-secret-value" not in json_credential.to_persisted_json()
    assert oauth_credential.status is ClauseStatus.BLOCKED
    assert "secret-oauth-value" not in oauth_credential.to_persisted_json()
    raw = RawEvidence(kind=EvidenceKind.CLI_PUBLIC_OUTPUT, summary=json_secret)
    assert raw.to_log_dict()["summary"] == "<redacted>"
    assert "super-secret-value" not in str(
        _cli_observation(contract, evidence=(raw,)).to_log_dict()
    )


def test_primary_assertion_failure_is_preserved_when_evidence_also_fails() -> None:
    contract = _contract()
    binding = _binding(contract)
    failed = _cli_observation(
        contract,
        exit_code=1,
        stdout="not ready",
        evidence=(RawEvidence(kind=EvidenceKind.CLI_EXIT_STATUS, summary="token=super-secret"),),
    )
    wrong_fixture = _cli_observation(contract, fixture_name="anti-cheat")
    result = evaluate_contract(contract, binding, [failed, wrong_fixture]).results[0]

    assert result.status is ClauseStatus.FAIL
    assert any(issue.code.value == "observation_binding" for issue in result.issues)
    assert {issue.code for issue in result.evidence_issues} == {
        EvidenceIssueCode.SENSITIVE,
        EvidenceIssueCode.MISSING,
    }


def test_full_stateless_reevaluation_retains_every_failure_and_out_of_scope_clause() -> None:
    fixtures = (_cli_fixture("first"), _cli_fixture("second"))
    clauses = (
        _clause("first-fails", "first"),
        _clause("second-fails", "second"),
    )
    contract = _contract(
        fixtures=fixtures,
        clauses=clauses,
        out_of_scope=(
            OutOfScopeClause(
                clause_id="not-evaluated",
                statement="A future integration is not evaluated.",
                reason="The integration is explicitly out of scope for v1.",
            ),
        ),
    )
    binding = _binding(contract)
    observations = [
        _cli_observation(contract, clause_id="first-fails", fixture_name="first", exit_code=1),
        _cli_observation(contract, clause_id="second-fails", fixture_name="second", exit_code=2),
    ]

    first = evaluate_contract(contract, binding, observations)
    second = evaluate_contract(contract, binding, observations)

    assert first == second
    assert [result.status for result in first.results] == [
        ClauseStatus.FAIL,
        ClauseStatus.FAIL,
        ClauseStatus.OUT_OF_SCOPE,
    ]
    assert not first.passed
    assert tuple(inspect.signature(evaluate_contract).parameters) == (
        "contract",
        "binding",
        "observations",
    )


def test_target_revision_change_requires_fresh_observations() -> None:
    contract = _contract()
    old_binding = _binding(contract)
    new_revision = "new-target-revision"
    new_binding = transition_contract(
        old_binding,
        contract,
        new_revision,
        _approval(contract, revision=new_revision, event_id="new-target"),
    )

    result = evaluate_contract(contract, new_binding, [_cli_observation(contract)]).results[0]

    assert result.status is ClauseStatus.BLOCKED


def test_all_five_closed_probe_surfaces_evaluate_deterministically() -> None:
    fixtures = (
        _cli_fixture(),
        Fixture(
            name="health",
            probe=HttpProbe(
                endpoint_reference="service-api",
                method=HttpMethod.GET,
                path="/health",
                expected_status=200,
                body_contains="ok",
            ),
        ),
        Fixture(
            name="manifest",
            probe=GeneratedArtifactProbe(
                path=ObservablePath("agent/generated/manifest.json"),
                assertion=GeneratedArtifactAssertion.EXISTS,
            ),
        ),
        Fixture(
            name="cache-absent",
            probe=FilesystemProbe(
                path=ObservablePath("output/cache.tmp"),
                assertion=FilesystemAssertion.ABSENT,
            ),
        ),
        Fixture(
            name="worker",
            probe=ProcessProbe(
                process_reference="approved-worker",
                assertion=ProcessAssertion.RUNNING,
            ),
        ),
    )
    clauses = (
        _clause(),
        ObservableClause(
            clause_id="health-ok",
            statement="The health API returns ok.",
            fixture_name="health",
            success_behavior="The API is healthy.",
            failure_behavior="The API is unhealthy.",
            required_evidence=(EvidenceKind.HTTP_STATUS, EvidenceKind.HTTP_PUBLIC_BODY),
        ),
        ObservableClause(
            clause_id="manifest-exists",
            statement="The generated manifest exists.",
            fixture_name="manifest",
            success_behavior="The manifest exists.",
            failure_behavior="The manifest is absent.",
            required_evidence=(EvidenceKind.ARTIFACT_EXISTENCE,),
        ),
        ObservableClause(
            clause_id="cache-absent",
            statement="The temporary cache is absent.",
            fixture_name="cache-absent",
            success_behavior="The cache is absent.",
            failure_behavior="The cache exists.",
            required_evidence=(EvidenceKind.FILESYSTEM_STATE,),
        ),
        ObservableClause(
            clause_id="worker-running",
            statement="The approved worker is running.",
            fixture_name="worker",
            success_behavior="The worker is running.",
            failure_behavior="The worker is not running.",
            required_evidence=(EvidenceKind.PROCESS_STATE,),
        ),
    )
    contract = _contract(
        fixtures=fixtures,
        clauses=clauses,
        roots=(ObservablePath("agent/generated"), ObservablePath("output")),
    )
    binding = _binding(contract)
    common = {
        "task_id": binding.task_id,
        "contract_identity": contract.identity,
        "target_revision": binding.target_revision,
    }
    observations = [
        _cli_observation(contract),
        HttpObservation(
            **common,
            clause_id="health-ok",
            fixture_name="health",
            status_code=200,
            public_body="ok",
            evidence=(
                RawEvidence(kind=EvidenceKind.HTTP_STATUS, summary="status=200"),
                RawEvidence(kind=EvidenceKind.HTTP_PUBLIC_BODY, summary="ok"),
            ),
        ),
        GeneratedArtifactObservation(
            **common,
            clause_id="manifest-exists",
            fixture_name="manifest",
            presence=Presence.PRESENT,
            evidence=(RawEvidence(kind=EvidenceKind.ARTIFACT_EXISTENCE, summary="present"),),
        ),
        FilesystemObservation(
            **common,
            clause_id="cache-absent",
            fixture_name="cache-absent",
            state=FilesystemState.ABSENT,
            evidence=(RawEvidence(kind=EvidenceKind.FILESYSTEM_STATE, summary="absent"),),
        ),
        ProcessObservation(
            **common,
            clause_id="worker-running",
            fixture_name="worker",
            state=ProcessState.RUNNING,
            evidence=(RawEvidence(kind=EvidenceKind.PROCESS_STATE, summary="running"),),
        ),
    ]

    report = evaluate_contract(contract, binding, observations)

    assert report.passed
    assert {result.status for result in report.results} == {ClauseStatus.PASS}


def test_process_observation_primitives_are_strict() -> None:
    contract = _contract()
    payload = {
        "probe_kind": "process",
        "task_id": "OSWE-34",
        "contract_identity": contract.identity.to_persisted_dict(),
        "target_revision": REVISION,
        "clause_id": "cli-works",
        "fixture_name": "cli-help",
        "state": True,
        "exit_code": 0,
    }
    with pytest.raises(ValidationError):
        observation_from_persisted_json(json.dumps(payload))


def test_public_observation_content_and_empty_evidence_fail_closed() -> None:
    contract = _contract()
    binding = _binding(contract)
    secret_output = _cli_observation(
        contract,
        stdout="ready Authorization: Bearer secret-stdout-token",
    )
    empty_evidence = _cli_observation(
        contract,
        evidence=(
            RawEvidence(kind=EvidenceKind.CLI_EXIT_STATUS, summary=" "),
            RawEvidence(kind=EvidenceKind.CLI_PUBLIC_OUTPUT, summary="	"),
        ),
    )

    secret_result = evaluate_contract(contract, binding, [secret_output]).results[0]
    empty_result = evaluate_contract(contract, binding, [empty_evidence]).results[0]

    assert secret_result.status is ClauseStatus.BLOCKED
    assert EvidenceIssueCode.SENSITIVE in {issue.code for issue in secret_result.evidence_issues}
    assert "secret-stdout-token" not in secret_result.to_persisted_json()
    assert secret_output.to_log_dict()["public_stdout"] == "<redacted>"
    assert empty_result.status is ClauseStatus.BLOCKED
    assert {issue.code for issue in empty_result.evidence_issues} == {EvidenceIssueCode.EMPTY}


def test_existence_probe_rejects_content_bearing_observation() -> None:
    fixture = Fixture(
        name="manifest",
        probe=GeneratedArtifactProbe(
            path=ObservablePath("agent/generated/manifest.json"),
            assertion=GeneratedArtifactAssertion.EXISTS,
        ),
    )
    clause = ObservableClause(
        clause_id="manifest-exists",
        statement="The generated manifest exists.",
        fixture_name="manifest",
        success_behavior="The manifest exists.",
        failure_behavior="The manifest is absent.",
        required_evidence=(EvidenceKind.ARTIFACT_EXISTENCE,),
    )
    contract = _contract(
        fixtures=(fixture,), clauses=(clause,), roots=(ObservablePath("agent/generated"),)
    )
    binding = _binding(contract)
    observation = GeneratedArtifactObservation(
        task_id=binding.task_id,
        contract_identity=contract.identity,
        target_revision=binding.target_revision,
        clause_id=clause.clause_id,
        fixture_name=fixture.name,
        presence=Presence.PRESENT,
        sha256="a" * 64,
        evidence=(RawEvidence(kind=EvidenceKind.ARTIFACT_EXISTENCE, summary="present"),),
    )

    result = evaluate_contract(contract, binding, [observation]).results[0]

    assert result.status is ClauseStatus.BLOCKED
    assert any(issue.code.value == "observation_binding" for issue in result.issues)
