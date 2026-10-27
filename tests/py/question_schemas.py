"""The question-research JSON Schemas in docs/contracts/question, loaded into one
registry so their `urn:isbe:question:` references resolve. Shared by the
contract tests and every test that checks a document against a contract."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "question"
SCHEMAS = {p.name.removesuffix(".schema.json"): json.loads(p.read_text()) for p in ROOT.glob("*.schema.json")}
REGISTRY = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in SCHEMAS.values())


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(SCHEMAS[name], registry=REGISTRY, format_checker=FormatChecker())


def errors(name: str, doc: object) -> list[str]:
    """What is wrong with `doc` as a `name` document; empty when it is valid."""
    return [e.message for e in validator(name).iter_errors(doc)]


def samples() -> list[dict]:
    """The fixed sample questions of samples.json."""
    return json.loads((ROOT / "samples.json").read_text())["questions"]
