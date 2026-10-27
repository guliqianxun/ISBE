"""Logging set up once at start-up (isbe.core.logs)."""

import logging

import pytest

from isbe.core import logs


@pytest.fixture
def root():
    logger = logging.getLogger()
    handlers, level = list(logger.handlers), logger.level
    yield logger
    logger.handlers[:] = handlers
    logger.setLevel(level)


def test_setup_installs_one_stderr_handler_at_the_chosen_level(root, capsys) -> None:
    assert logs.setup({}) == logging.INFO
    assert logs.setup({"ISBE_LOG_LEVEL": "warning"}) == logging.WARNING
    assert sum(isinstance(h, logs._Stderr) for h in root.handlers) == 1
    logs.setup({})
    logging.getLogger("isbe.question.flow").info("question run r1: plan step started")
    logging.getLogger("httpx").info("HTTP Request: GET http://essaies.test/api/search?q=secret")
    err = capsys.readouterr().err
    assert " INFO isbe.question.flow: question run r1: plan step started" in err
    assert "HTTP Request" not in err


def test_an_unknown_level_falls_back_to_info_and_says_so(root, capsys) -> None:
    assert logs.setup({"ISBE_LOG_LEVEL": "chatty"}) == logging.INFO
    assert "ISBE_LOG_LEVEL='CHATTY' is not one of" in capsys.readouterr().err
