"""Schema validation only. A valid shape is not a correct extraction."""

from __future__ import annotations

from pydantic import ValidationError

from schemas.medical_schema import Claim


def validate_schema(payload: dict) -> dict:
    try:
        claim = Claim.model_validate(payload)
    except ValidationError as exc:
        return {"schema_valid": False, "errors": exc.errors()}
    return {"schema_valid": True, "errors": [], "claim": claim.model_dump(mode="json")}
