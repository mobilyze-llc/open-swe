from __future__ import annotations

from dataclasses import dataclass

from agent.mobilyze.behavior.policy import require_sha256, require_text
from agent.mobilyze.behavior.report import ClauseResult


@dataclass(frozen=True, slots=True)
class CacheKey:
    target_artifact_hash: str
    clause_hash: str
    executor_version: str
    profile_image_hash: str

    def __post_init__(self) -> None:
        require_sha256(self.target_artifact_hash, "cache target/artifact hash")
        require_sha256(self.clause_hash, "cache clause hash")
        require_sha256(self.profile_image_hash, "cache profile/image hash")
        require_text(self.executor_version, "cache executor version")


class ClauseCache:
    def __init__(self) -> None:
        self._entries: dict[CacheKey, ClauseResult] = {}

    def get(self, key: CacheKey) -> ClauseResult | None:
        return self._entries.get(key)

    def put(self, key: CacheKey, result: ClauseResult) -> None:
        self._entries[key] = result

    def __len__(self) -> int:
        return len(self._entries)
