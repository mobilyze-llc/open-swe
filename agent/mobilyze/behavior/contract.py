from __future__ import annotations

import hashlib
import json
from typing import Literal, Self

from pydantic import Field, model_validator

from agent.mobilyze.behavior.core import (
    BehaviorModel,
    Identifier,
    LongText,
    Sha256,
    ShortText,
)
from agent.mobilyze.behavior.paths import ObservablePath
from agent.mobilyze.behavior.probes import (
    EvidenceKind,
    FilesystemProbe,
    Fixture,
    GeneratedArtifactProbe,
    supported_evidence,
)

SCHEMA_VERSION = "mobilyze.behavior-contract.v1"


class ContractIdentity(BehaviorModel):
    schema_version: Literal["mobilyze.behavior-contract.v1"] = SCHEMA_VERSION
    contract_id: Identifier
    version: int = Field(ge=1)
    content_hash: Sha256


class TargetAccessReference(BehaviorModel):
    target: str = Field(min_length=1, max_length=1024)
    access_reference: Identifier


class ObservableClause(BehaviorModel):
    clause_id: Identifier
    statement: LongText
    fixture_name: Identifier
    success_behavior: ShortText
    failure_behavior: ShortText
    required_evidence: tuple[EvidenceKind, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _validate_evidence_uniqueness(self) -> ObservableClause:
        if len(set(self.required_evidence)) != len(self.required_evidence):
            raise ValueError("required evidence kinds must be unique")
        return self


class OutOfScopeClause(BehaviorModel):
    clause_id: Identifier
    statement: LongText
    reason: ShortText


class BehaviorContract(BehaviorModel):
    schema_version: Literal["mobilyze.behavior-contract.v1"] = SCHEMA_VERSION
    contract_id: Identifier
    version: int = Field(ge=1)
    goal: LongText
    target: TargetAccessReference
    approved_credential_references: tuple[Identifier, ...] = Field(default=(), max_length=32)
    observable_output_roots: tuple[ObservablePath, ...] = Field(default=(), max_length=32)
    fixtures: tuple[Fixture, ...] = Field(min_length=1, max_length=64)
    clauses: tuple[ObservableClause, ...] = Field(min_length=1, max_length=128)
    out_of_scope_clauses: tuple[OutOfScopeClause, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def _validate_contract(self) -> Self:
        self._require_unique("credential reference", self.approved_credential_references)
        self._require_unique(
            "output root", tuple(str(root) for root in self.observable_output_roots)
        )
        fixture_names = tuple(fixture.name for fixture in self.fixtures)
        self._require_unique("fixture name", fixture_names)
        clause_ids = tuple(clause.clause_id for clause in self.clauses)
        out_of_scope_ids = tuple(clause.clause_id for clause in self.out_of_scope_clauses)
        self._require_unique("clause ID", clause_ids + out_of_scope_ids)
        fixtures = {fixture.name: fixture for fixture in self.fixtures}
        approved_credentials = set(self.approved_credential_references)
        for fixture in self.fixtures:
            if len(set(fixture.credential_references)) != len(fixture.credential_references):
                raise ValueError(f"fixture '{fixture.name}' repeats a credential reference")
            unknown_credentials = set(fixture.credential_references) - approved_credentials
            if unknown_credentials:
                raise ValueError(
                    f"fixture '{fixture.name}' uses an unapproved credential reference"
                )
            probe = fixture.probe
            if isinstance(probe, GeneratedArtifactProbe | FilesystemProbe) and not any(
                root.contains(probe.path) for root in self.observable_output_roots
            ):
                raise ValueError(f"fixture '{fixture.name}' path is outside declared output roots")
        for clause in self.clauses:
            fixture = fixtures.get(clause.fixture_name)
            if fixture is None:
                raise ValueError(f"clause '{clause.clause_id}' references an unknown fixture")
            unsupported = set(clause.required_evidence) - supported_evidence(fixture.probe)
            if unsupported:
                raise ValueError(f"clause '{clause.clause_id}' requires unsupported evidence")
        return self

    @staticmethod
    def _require_unique(label: str, values: tuple[object, ...]) -> None:
        if len(set(values)) != len(values):
            raise ValueError(f"{label}s must be unique")

    def canonical_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json"))

    def canonical_content_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json", exclude={"version"}))

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_content_json().encode()).hexdigest()

    @property
    def identity(self) -> ContractIdentity:
        return ContractIdentity(
            contract_id=self.contract_id,
            version=self.version,
            content_hash=self.content_hash,
        )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
