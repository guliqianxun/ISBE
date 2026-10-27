"""The settings file people import and export (`isbe settings`, the Settings page).

Version 1 is `{"format": "isbe-settings", "version": 1, "settings": {...}}`; a
bare object of names (what `<data>/settings.json` holds) is read the same way.
Names are `settings.KEYS`. A whole number or boolean is read as its text and
`ISBE_LLM_EXTRA_BODY` may be an object; null or "" clears a saved value.
frontend/src/settings/file.ts reads the same format, and both suites read
examples/settings.example.json.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from isbe.core import jsonfmt, settings
from isbe.core.validators import Invalid

FORMAT = "isbe-settings"
VERSION = 1
# The largest whole number JavaScript reads exactly (Number.MAX_SAFE_INTEGER).
SAFE_INTEGER = 2**53 - 1


@dataclass
class Parsed:
    changes: dict[str, str | None] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)


def parse(raw: bytes) -> Parsed:
    """The changes a settings file asks for; `Invalid` when it is not one.

    Unknown names are listed, not changed; `settings.RETIRED` names are dropped
    quietly. Values are not validated here: `settings.save` does that for the
    whole file before writing anything.
    """
    if len(raw) > settings.MAX_FILE:
        raise Invalid(f"the settings file is larger than {settings.MAX_FILE // 1024} KB")
    try:
        doc = jsonfmt.loads(raw)
    except ValueError:
        raise Invalid("the settings file is not valid JSON") from None
    if not isinstance(doc, dict):
        raise Invalid("the settings file must be a JSON object")
    if "format" in doc:
        if doc["format"] != FORMAT:
            raise Invalid(f'"format" must be "{FORMAT}"')
        version = doc.get("version")
        if type(version) is not int or version != VERSION:
            raise Invalid(f"only version {VERSION} settings files can be read")
        doc = doc.get("settings")
        if not isinstance(doc, dict):
            raise Invalid('"settings" must be an object of setting names')
    parsed = Parsed()
    for name in sorted(doc):
        if name in settings.RETIRED:
            continue
        if name not in settings.KEYS:
            parsed.unknown.append(name)
            continue
        parsed.changes[name] = _text(name, doc[name])
    return parsed


def _text(name: str, value: Any) -> str | None:
    if value is None or isinstance(value, str):
        return value or None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        # Every numeric setting is a whole number; `900.0` is 900, as in JavaScript.
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, int) or abs(value) > SAFE_INTEGER:
            raise Invalid(f"{name} must be a whole number")
        return str(value)
    if isinstance(value, dict) and name == "ISBE_LLM_EXTRA_BODY":
        return jsonfmt.dumps(_as_javascript(value))
    raise Invalid(f"{name} must be a string, number, boolean or null")


def _as_javascript(value: Any) -> Any:
    """`value` with whole floats as ints, so its JSON text is what
    `JSON.stringify` writes for it (`1.0` → `1`; from 1e21 on both use exponents)."""
    if isinstance(value, dict):
        return {k: _as_javascript(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_as_javascript(v) for v in value]
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        return int(value)
    return value


def export(data: Path, secrets: bool = False) -> tuple[dict[str, Any], list[str]]:
    """The settings `data` runs with (saved values, environment overrides on
    top, defaults left out) as a version 1 file, and the names of environment
    values left out because they are invalid (the saved value, if any, stays).

    Secrets are left out unless `secrets`; the names of the ones left out are
    listed in `secrets_omitted` so the reader knows what to add.
    """
    saved = settings.read(data)
    invalid = []
    for name in settings.KEYS:
        try:
            value = settings.validate(name, settings.environment(name))
        except Invalid:
            invalid.append(name)
            continue
        if value is not None:
            saved[name] = value
    saved = dict(sorted(saved.items()))
    omitted = sorted(n for n in saved if n in settings.SECRETS and not secrets)
    doc: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "settings": {n: v for n, v in saved.items() if n not in omitted},
    }
    if omitted:
        doc["secrets_omitted"] = omitted
    return doc, invalid
