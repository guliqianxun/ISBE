"""Files shipped beside the code: migrations, prompts, the web UI and the help pages
(the READMEs and documents).

An installed wheel carries them under `isbe/_assets`; a checkout keeps them at
the repository root, so edits there apply without a rebuild.
"""

from functools import cache
from pathlib import Path

_PACKAGED = Path(__file__).resolve().parents[1] / "_assets"
_CHECKOUT = Path(__file__).resolve().parents[2]


@cache
def path(*parts: str) -> Path:
    for base in (_PACKAGED, _CHECKOUT):
        candidate = base.joinpath(*parts)
        if candidate.exists():
            return candidate
    raise FileNotFoundError("/".join(parts))


def text(*parts: str) -> str:
    return path(*parts).read_text(encoding="utf-8")
