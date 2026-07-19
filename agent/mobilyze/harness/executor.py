from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from agent.mobilyze.harness.contracts import (
    ExecutionHandle,
    HarnessResult,
    HarnessSpec,
    PromptSource,
)
from agent.mobilyze.harness.events import HarnessEvent


@runtime_checkable
class HarnessExecutor(Protocol):
    async def start(self, spec: HarnessSpec) -> ExecutionHandle: ...

    def events(self, handle: ExecutionHandle) -> AsyncIterator[HarnessEvent]: ...

    async def resume(
        self, handle: ExecutionHandle, prompt_source: PromptSource
    ) -> ExecutionHandle: ...

    async def cancel(self, handle: ExecutionHandle) -> None: ...

    async def result(self, handle: ExecutionHandle) -> HarnessResult: ...
