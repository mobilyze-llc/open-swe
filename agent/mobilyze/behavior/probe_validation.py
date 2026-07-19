from __future__ import annotations

from agent.mobilyze.behavior.models import (
    ArtifactProbe,
    CliProbe,
    ContractValidationError,
    EvidenceType,
    FileEffect,
    HttpProbe,
    JsonFieldExpectation,
    Probe,
    ProcessProbe,
)
from agent.mobilyze.behavior.policy import require_text

_COLLECTION_TYPES: tuple[tuple[str, type[object]], ...] = (
    ("stdout_contains", str),
    ("stderr_contains", str),
    ("stdout_fields", JsonFieldExpectation),
    ("filesystem_effects", FileEffect),
    ("response_fields", JsonFieldExpectation),
    ("persistence_fields", JsonFieldExpectation),
    ("contains", str),
    ("fields", JsonFieldExpectation),
    ("public_log_contains", str),
)


def validate_probe_collections(probe: Probe) -> None:
    for field_name, item_type in _COLLECTION_TYPES:
        if not hasattr(probe, field_name):
            continue
        value = getattr(probe, field_name)
        if not isinstance(value, tuple):
            raise ContractValidationError(f"{field_name} must be an immutable tuple")
        for item in value:
            if not isinstance(item, item_type):
                raise ContractValidationError(
                    f"{field_name} must contain {item_type.__name__} values"
                )
            if item_type is str:
                assert isinstance(item, str)
                try:
                    require_text(item, f"{field_name} marker")
                except ValueError as error:
                    raise ContractValidationError(str(error)) from error


def validate_evidence_types(probe: Probe, evidence_types: tuple[EvidenceType, ...]) -> None:
    if not evidence_types:
        raise ContractValidationError("in-scope clauses require explicit evidence types")
    if len(set(evidence_types)) != len(evidence_types):
        raise ContractValidationError("evidence types must be unique")

    supported = _supported_evidence_types(probe)
    unsupported = sorted(item.value for item in set(evidence_types).difference(supported))
    if unsupported:
        raise ContractValidationError(
            f"probe does not assert declared evidence types: {', '.join(unsupported)}"
        )


def _supported_evidence_types(probe: Probe) -> frozenset[EvidenceType]:
    if isinstance(probe, CliProbe):
        supported = {EvidenceType.EXIT_CODE}
        if probe.stdout_contains or probe.stderr_contains or probe.stdout_fields:
            supported.add(EvidenceType.PUBLIC_OUTPUT)
        if probe.filesystem_effects:
            supported.add(EvidenceType.FILESYSTEM_EFFECT)
        return frozenset(supported)
    if isinstance(probe, HttpProbe):
        supported = {EvidenceType.HTTP_RESPONSE}
        if probe.persistence_fields:
            supported.add(EvidenceType.PERSISTENCE)
        return frozenset(supported)
    if isinstance(probe, ArtifactProbe):
        supported = {EvidenceType.ARTIFACT_CONTENT}
        if probe.expected_sha256 is not None:
            supported.add(EvidenceType.ARTIFACT_HASH)
        return frozenset(supported)
    if isinstance(probe, ProcessProbe):
        supported = {EvidenceType.PROCESS_LIFECYCLE}
        if probe.public_log_contains:
            supported.add(EvidenceType.PUBLIC_LOG)
        return frozenset(supported)
    raise AssertionError("approved probe type must have an evidence mapping")
