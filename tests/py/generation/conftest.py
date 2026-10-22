from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from generation_helpers import Answer, Script, fake_stages

from isbe.core import settings
from isbe.generation import Manager, Stages


@pytest.fixture
def script(serve: Any) -> Callable[[Answer], Script]:
    """`script(answer)`: a local server answering request `i` with `answer(i, request)`."""

    def start(answer: Answer) -> Script:
        s = Script(answer)
        s.base = serve(s.handle)
        return s

    return start


@pytest.fixture
def managers() -> Iterator[Callable[..., Manager]]:
    """`open(root, stages=fake_stages(), read_settings=settings.read)`, closed at teardown."""
    opened: list[Manager] = []

    def open(root: Path, *, stages: Stages | None = None, **options: Any) -> Manager:
        options.setdefault("read_settings", settings.read)
        manager = Manager.open(root, stages=stages or fake_stages(), **options)
        opened.append(manager)
        return manager

    yield open
    for manager in opened:
        manager.close(timeout=30)


@pytest.fixture(params=["doubles", "real"])
def stages(request: pytest.FixtureRequest) -> Stages:
    """The pipeline of a worker test: the doubles, or the real stages."""
    if request.param == "real":
        return Stages()
    return fake_stages()
