from __future__ import annotations

from pydantic import ConfigDict, RootModel, field_validator
from pydantic_core import PydanticCustomError

_FORBIDDEN_COMPONENTS = frozenset(
    {
        ".git",
        "diff",
        "diffs",
        "histories",
        "history",
        "source",
        "sources",
        "src",
        "test",
        "tests",
        "trace",
        "traces",
        "transcript",
        "transcripts",
    }
)


class ObservablePath(RootModel[str]):
    """Validated relative path for declared observable output."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True, strict=True)

    @field_validator("root")
    @classmethod
    def _validate_root(cls, value: str) -> str:
        if not value or value == ".":
            raise PydanticCustomError(
                "observable_path_empty", "observable path cannot be empty or '.'"
            )
        if len(value) > 512:
            raise PydanticCustomError("observable_path_overlong", "observable path is too long")
        if value.startswith("/") or value.startswith("\\"):
            raise PydanticCustomError(
                "observable_path_absolute", "observable path must be relative"
            )
        if "\\" in value or "\x00" in value:
            raise PydanticCustomError("observable_path_malformed", "observable path is malformed")
        components = value.split("/")
        if any(component in {"", "."} for component in components):
            raise PydanticCustomError("observable_path_malformed", "observable path is malformed")
        if any(component == ".." for component in components):
            raise PydanticCustomError(
                "observable_path_escape", "observable path cannot escape its root"
            )
        forbidden = next(
            (
                component
                for component in components
                if component.casefold() in _FORBIDDEN_COMPONENTS
            ),
            None,
        )
        if forbidden is not None:
            raise PydanticCustomError(
                "observable_path_forbidden",
                "observable path contains forbidden component '{component}'",
                {"component": forbidden},
            )
        return value

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(self.root.split("/"))

    def contains(self, candidate: ObservablePath) -> bool:
        return candidate.parts[: len(self.parts)] == self.parts

    def __str__(self) -> str:
        return self.root
