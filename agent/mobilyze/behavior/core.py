from __future__ import annotations

from typing import Annotated

from pydantic import Field

from agent.mobilyze.harness.contracts import PersistedContract

Identifier = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
]
ShortText = Annotated[str, Field(min_length=1, max_length=512)]
LongText = Annotated[str, Field(min_length=1, max_length=4096)]
Revision = Annotated[str, Field(min_length=1, max_length=256)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class BehaviorModel(PersistedContract):
    """Strict immutable behavior-contract model."""
