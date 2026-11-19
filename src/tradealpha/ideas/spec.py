"""
IdeaSpec v1 (Phase 2.1): the contract between the idea generation engine and Historify.

* canonical form = JSON with sorted keys and no whitespace, ``signature`` removed;
* ``spec_sha`` = sha256 of the canonical form - the identity of the submission (same spec = same idea version);
* ``signature`` = HMAC-SHA256 (hex) of the canonical form with the shared secret (setting ``ideas_intake_secret``);
* the IR document is the Strategy IR v1 (compiled and causality-checked by the validation job);
* ``trials_tested`` is the number of variants the GENERATOR tried before submitting - it deflates the Sharpe
  (Phase 2.4), so under-reporting it makes the evaluation stricter for later ideas, never looser for this one;
* ``source.data_end`` is the last day of data the generator saw - the embargo check refuses ideas that saw the
  hold-out period.
"""
from __future__ import annotations

import hashlib
import hmac as hmaclib
import json
import re

SPEC_VERSION = 1
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{2,63}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SCHEMA = {
    "type": "object",
    "required": ["spec_version", "name", "ir", "universe", "source", "signature"],
    "additionalProperties": False,
    "properties": {
        "spec_version": {"const": SPEC_VERSION},
        "name": {"type": "string", "pattern": NAME_RE.pattern},
        "ir": {"type": "object"},                               # Strategy IR v1 (validated by its own schema + compiler)
        "params": {"type": "object"},
        "universe": {"type": "object", "required": ["kind"], "additionalProperties": False, "properties": {
            "kind": {"enum": ["symbols", "watchlist", "index"]},
            "symbols": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
            "name": {"type": "string"}, "index": {"type": "string"}}},
        "capital_hint": {"type": "number", "exclusiveMinimum": 0},
        "claimed": {"type": "object", "additionalProperties": False, "properties": {
            "sharpe": {"type": "number"}, "cagr_pct": {"type": "number"},
            "max_drawdown_pct": {"type": "number"}, "trades": {"type": "integer", "minimum": 0}}},
        "trials_tested": {"type": "integer", "minimum": 0, "maximum": 10_000_000},
        "source": {"type": "object", "required": ["generator", "data_end"], "additionalProperties": False,
                   "properties": {"generator": {"type": "string", "maxLength": 64},
                                  "run_id": {"type": "string", "maxLength": 100},
                                  "data_end": {"type": "string", "pattern": DATE_RE.pattern}}},
        "parent": {"type": "object", "additionalProperties": False, "properties": {
            "idea_id": {"type": "string"}, "version": {"type": "integer", "minimum": 1}}},
        "notes": {"type": "string", "maxLength": 4000},
        "signature": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    },
}


class SpecError(ValueError):
    def __init__(self, message: str, pointer: str = "", status: int = 400):
        super().__init__(message)
        self.pointer = pointer
        self.status = status


def canonical(spec: dict) -> bytes:
    """The signed / hashed form: sorted keys, no whitespace, the signature itself excluded."""
    body = {k: v for k, v in spec.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def spec_sha(spec: dict) -> str:
    return hashlib.sha256(canonical(spec)).hexdigest()


def sign(spec: dict, secret: str) -> str:
    return hmaclib.new(secret.encode(), canonical(spec), hashlib.sha256).hexdigest()


def verify_signature(spec: dict, secret: str | None) -> None:
    if not secret:
        raise SpecError("idea intake is not configured: set the shared secret first "
                        "(PUT /api/v2/ideas/secret or Settings)", "/signature", 503)
    given = str(spec.get("signature") or "")
    if not hmaclib.compare_digest(given, sign(spec, secret)):
        raise SpecError("the signature does not match the canonical spec (HMAC-SHA256 with the shared secret)",
                        "/signature", 401)


def validate_schema(spec: dict) -> None:
    """Schema check with a JSON-Pointer in the error (jsonschema when available, else a structural check)."""
    try:
        import jsonschema
    except ImportError:                                         # pragma: no cover - bundled in requirements-lab
        _fallback_validate(spec)
        return
    v = jsonschema.Draft202012Validator(SCHEMA)
    err = next(iter(sorted(v.iter_errors(spec), key=lambda e: list(e.absolute_path))), None)
    if err is not None:
        ptr = "/" + "/".join(str(x) for x in err.absolute_path)
        raise SpecError(f"IdeaSpec v{SPEC_VERSION}: {err.message}", ptr)


def _fallback_validate(spec: dict) -> None:
    for k in SCHEMA["required"]:
        if k not in spec:
            raise SpecError(f"IdeaSpec v{SPEC_VERSION}: '{k}' is required", f"/{k}")
    if spec.get("spec_version") != SPEC_VERSION:
        raise SpecError(f"spec_version must be {SPEC_VERSION}", "/spec_version")
    if not NAME_RE.match(str(spec.get("name") or "")):
        raise SpecError("name must be a slug (a-z, 0-9, _, -; 3..64 chars)", "/name")
    if not DATE_RE.match(str((spec.get("source") or {}).get("data_end") or "")):
        raise SpecError("source.data_end must be YYYY-MM-DD", "/source/data_end")


def summary(spec: dict) -> dict:
    """The stored / listed view (without the IR body)."""
    return {"name": spec["name"], "universe": spec.get("universe"), "params": spec.get("params") or {},
            "capital_hint": spec.get("capital_hint"), "claimed": spec.get("claimed") or {},
            "trials_tested": int(spec.get("trials_tested") or 0), "source": spec.get("source"),
            "parent": spec.get("parent"), "notes": spec.get("notes")}
