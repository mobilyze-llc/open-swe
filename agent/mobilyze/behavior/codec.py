from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, TypeVar

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
    Probe,
    ProbeType,
    ProcessProbe,
    TargetRef,
    TargetType,
)

EnumT = TypeVar("EnumT", bound=Enum)


def canonical_data(value: object) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: canonical_data(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple | list):
        return [canonical_data(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ContractValidationError("canonical mappings require string keys")
        return {key: canonical_data(item) for key, item in sorted(value.items())}
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ContractValidationError("canonical numbers must be finite")
        return value
    raise ContractValidationError(f"unsupported canonical value type: {type(value).__name__}")


def canonical_json(value: object) -> str:
    return json.dumps(
        canonical_data(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def content_sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ContractValidationError(f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ContractValidationError(f"{field} must be an array")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(f"{field} must be a string")
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractValidationError(f"{field} must be an integer")
    return value


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ContractValidationError(f"{field} must be a boolean")
    return value


def _optional_text(value: object, field: str) -> str | None:
    return None if value is None else _text(value, field)


def _enum(enum_type: type[EnumT], value: object, field: str) -> EnumT:
    try:
        return enum_type(_text(value, field))
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise ContractValidationError(f"{field} must be one of: {allowed}") from error


def _exact(value: dict[str, object], expected: set[str], field: str) -> None:
    unknown = sorted(value.keys() - expected)
    missing = sorted(expected - value.keys())
    if unknown:
        raise ContractValidationError(f"{field} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ContractValidationError(f"{field} is missing fields: {', '.join(missing)}")


def _field_expectation(value: object) -> JsonFieldExpectation:
    data = _object(value, "JSON field expectation")
    _exact(data, {"path", "kind", "compare_value", "expected"}, "JSON field expectation")
    expected = data["expected"]
    if not (expected is None or isinstance(expected, str | int | float | bool)):
        raise ContractValidationError("JSON expected value must be a scalar")
    return JsonFieldExpectation(
        path=tuple(
            _text(part, "JSON field path component") for part in _list(data["path"], "path")
        ),
        kind=_enum(JsonKind, data["kind"], "JSON field kind"),
        expected=expected,
        compare_value=_boolean(data["compare_value"], "compare_value"),
    )


def _file_effect(value: object) -> FileEffect:
    data = _object(value, "filesystem effect")
    _exact(data, {"path", "state", "sha256"}, "filesystem effect")
    return FileEffect(
        path=_text(data["path"], "filesystem effect path"),
        state=_enum(FileState, data["state"], "filesystem effect state"),
        sha256=_optional_text(data["sha256"], "filesystem effect hash"),
    )


def _probe(value: object) -> Probe:
    data = _object(value, "probe")
    probe_type = _enum(ProbeType, data.get("type"), "probe type")
    if probe_type is ProbeType.CLI:
        expected = {
            "type",
            "fixture",
            "expected_exit_code",
            "stdout_contains",
            "stderr_contains",
            "stdout_fields",
            "filesystem_effects",
        }
        _exact(data, expected, "CLI probe")
        return CliProbe(
            fixture=_text(data["fixture"], "fixture"),
            expected_exit_code=_integer(data["expected_exit_code"], "expected_exit_code"),
            stdout_contains=tuple(
                _text(item, "stdout marker")
                for item in _list(data["stdout_contains"], "stdout_contains")
            ),
            stderr_contains=tuple(
                _text(item, "stderr marker")
                for item in _list(data["stderr_contains"], "stderr_contains")
            ),
            stdout_fields=tuple(
                _field_expectation(item) for item in _list(data["stdout_fields"], "stdout_fields")
            ),
            filesystem_effects=tuple(
                _file_effect(item)
                for item in _list(data["filesystem_effects"], "filesystem_effects")
            ),
        )
    if probe_type is ProbeType.HTTP_API:
        _exact(
            data,
            {"type", "fixture", "expected_status", "response_fields", "persistence_fields"},
            "HTTP probe",
        )
        return HttpProbe(
            fixture=_text(data["fixture"], "fixture"),
            expected_status=_integer(data["expected_status"], "expected_status"),
            response_fields=tuple(
                _field_expectation(item)
                for item in _list(data["response_fields"], "response_fields")
            ),
            persistence_fields=tuple(
                _field_expectation(item)
                for item in _list(data["persistence_fields"], "persistence_fields")
            ),
        )
    if probe_type is ProbeType.GENERATED_ARTIFACT:
        _exact(
            data,
            {"type", "fixture", "path", "expected_sha256", "contains", "fields"},
            "artifact probe",
        )
        return ArtifactProbe(
            fixture=_text(data["fixture"], "fixture"),
            path=_text(data["path"], "artifact path"),
            expected_sha256=_optional_text(data["expected_sha256"], "artifact hash"),
            contains=tuple(
                _text(item, "content marker") for item in _list(data["contains"], "contains")
            ),
            fields=tuple(_field_expectation(item) for item in _list(data["fields"], "fields")),
        )
    _exact(
        data,
        {
            "type",
            "fixture",
            "expected_launched",
            "expected_terminated",
            "expected_exit_code",
            "public_log_contains",
        },
        "process probe",
    )
    exit_code = data["expected_exit_code"]
    return ProcessProbe(
        fixture=_text(data["fixture"], "fixture"),
        expected_launched=_boolean(data["expected_launched"], "expected_launched"),
        expected_terminated=_boolean(data["expected_terminated"], "expected_terminated"),
        expected_exit_code=None if exit_code is None else _integer(exit_code, "expected_exit_code"),
        public_log_contains=tuple(
            _text(item, "public log marker")
            for item in _list(data["public_log_contains"], "public_log_contains")
        ),
    )


def _clause(value: object) -> ContractClause:
    data = _object(value, "clause")
    _exact(
        data,
        {
            "id",
            "task",
            "expected_behavior",
            "failure_behavior",
            "evidence_types",
            "anti_cheat",
            "out_of_scope_reason",
            "adjacent_clause_ids",
            "probe",
        },
        "clause",
    )
    probe_value = data["probe"]
    return ContractClause(
        id=_text(data["id"], "clause id"),
        task=_text(data["task"], "clause task"),
        expected_behavior=_text(data["expected_behavior"], "expected behavior"),
        failure_behavior=_text(data["failure_behavior"], "failure behavior"),
        evidence_types=tuple(
            _enum(EvidenceType, item, "evidence type")
            for item in _list(data["evidence_types"], "evidence_types")
        ),
        probe=None if probe_value is None else _probe(probe_value),
        anti_cheat=_boolean(data["anti_cheat"], "anti_cheat"),
        out_of_scope_reason=_optional_text(data["out_of_scope_reason"], "out-of-scope reason"),
        adjacent_clause_ids=tuple(
            _text(item, "adjacent clause id")
            for item in _list(data["adjacent_clause_ids"], "adjacent_clause_ids")
        ),
    )


def contract_from_dict(value: object) -> BehaviorContract:
    data = _object(value, "contract")
    _exact(
        data,
        {
            "schema",
            "contract_version",
            "owner",
            "user_visible_goal",
            "target",
            "approved_fixtures",
            "credential_references",
            "clauses",
        },
        "contract",
    )
    owner = _object(data["owner"], "owner")
    target = _object(data["target"], "target")
    _exact(owner, {"type", "reference"}, "owner")
    _exact(target, {"type", "reference"}, "target")
    return BehaviorContract(
        schema=_text(data["schema"], "schema"),
        contract_version=_integer(data["contract_version"], "contract_version"),
        owner=ContractOwner(
            _enum(OwnerType, owner["type"], "owner type"),
            _text(owner["reference"], "owner reference"),
        ),
        user_visible_goal=_text(data["user_visible_goal"], "user_visible_goal"),
        target=TargetRef(
            _enum(TargetType, target["type"], "target type"),
            _text(target["reference"], "target reference"),
        ),
        approved_fixtures=tuple(
            _text(item, "approved fixture")
            for item in _list(data["approved_fixtures"], "approved_fixtures")
        ),
        credential_references=tuple(
            _text(item, "credential reference")
            for item in _list(data["credential_references"], "credential_references")
        ),
        clauses=tuple(_clause(item) for item in _list(data["clauses"], "clauses")),
    )
