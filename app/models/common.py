"""Base classes and shared schemas for the public API (JSON in camelCase, PLAN.md §6.1)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from pydantic_core import PydanticCustomError


class RequestModel(BaseModel):
    """Client input: camelCase names only, unknown fields are rejected (FR-02)."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        # Also serialise by alias: request models are echoed in results, and a parent model's
        # serialize_by_alias does not carry over to nested models of another class.
        serialize_by_alias=True,
        extra="forbid",
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_python_names(cls, data: Any) -> Any:
        # With validate_by_name=False pydantic silently *ignores* a snake_case field name instead of treating it
        # as an extra field, so `{"collision_detection": true}` would pass with the default value. Reject it.
        if isinstance(data, dict):
            misspelt = {
                key: field.alias
                for key, field in cls.model_fields.items()
                if key in data and field.alias and field.alias != key
            }
            if misspelt:
                hint = ", ".join(f"{k!r} (use {v!r})" for k, v in misspelt.items())
                raise PydanticCustomError(
                    "extra_forbidden", "Unknown field name(s): {hint}. Field names are camelCase.", {"hint": hint}
                )
        return data


class ResponseModel(BaseModel):
    """Server output: built from snake_case names in Python, serialised as camelCase."""

    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_alias=True, validate_by_name=True, serialize_by_alias=True
    )


class ErrorDetail(ResponseModel):
    loc: list[str | int] = Field(description='Path to the offending field, e.g. `["config", "timestep"]`.')
    message: str
    type: str


class ErrorResponse(ResponseModel):
    code: str = Field(description="Stable, machine-readable error code, e.g. `validation_error`.")
    message: str = Field(description="Human-readable explanation.")
    details: list[ErrorDetail] = Field(default_factory=list)


class HealthResponse(ResponseModel):
    status: str = Field(examples=["ok"])
