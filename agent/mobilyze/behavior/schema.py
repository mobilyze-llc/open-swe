from enum import StrEnum
from typing import TypeAlias

CONTRACT_SCHEMA = "mobilyze.behavior-contract.v1"
JsonScalar: TypeAlias = str | int | float | bool | None


class OwnerType(StrEnum):
    ISSUE = "issue"
    APPROVED_PLAN = "approved_plan"


class TargetType(StrEnum):
    CLI = "cli"
    HTTP_API = "http_api"
    ARTIFACT = "artifact"
    PROCESS = "process"


class ProbeType(StrEnum):
    CLI = "cli"
    HTTP_API = "http_api"
    GENERATED_ARTIFACT = "generated_artifact"
    PROCESS = "process"


class EvidenceType(StrEnum):
    EXIT_CODE = "exit_code"
    PUBLIC_OUTPUT = "public_output"
    FILESYSTEM_EFFECT = "filesystem_effect"
    HTTP_RESPONSE = "http_response"
    PERSISTENCE = "persistence"
    ARTIFACT_HASH = "artifact_hash"
    ARTIFACT_CONTENT = "artifact_content"
    PROCESS_LIFECYCLE = "process_lifecycle"
    PUBLIC_LOG = "public_log"


class JsonKind(StrEnum):
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"
    NULL = "null"


class FileState(StrEnum):
    EXISTS = "exists"
    ABSENT = "absent"
