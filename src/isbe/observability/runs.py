import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from isbe.facts.artifacts import TopicRun
from isbe.facts.db import make_session_factory

logger = logging.getLogger(__name__)


@dataclass
class _RunHandle:
    payload: dict = field(default_factory=dict)


def _persist_run(
    *,
    topic_id: str,
    flow_name: str,
    status: str,
    started: datetime,
    finished: datetime,
    payload: dict,
) -> None:
    Session = make_session_factory()
    with Session() as s:
        s.add(
            TopicRun(
                id=uuid4(),
                topic_id=topic_id,
                flow_name=flow_name,
                status=status,
                started_at=started,
                finished_at=finished,
                payload=payload,
            )
        )
        s.commit()


@contextmanager
def topic_run(topic_id: str, flow_name: str):
    """Records a TopicRun row with start/finish/status.

    Yields a handle whose .payload dict is persisted to the row's payload column.
    On exception inside the block, status='failed' and error is recorded; exception re-raised.

    If persisting the row itself fails while the flow body also failed, the
    persist failure is logged and the ORIGINAL flow exception propagates —
    an unreachable DB must not mask the reason the run failed.
    """
    started = datetime.now(UTC)
    handle = _RunHandle()
    status = "ok"
    err: str | None = None
    try:
        yield handle
    except Exception as e:
        status = "failed"
        err = f"{type(e).__name__}: {e}"
        raise
    finally:
        finished = datetime.now(UTC)
        payload = dict(handle.payload)
        if err:
            payload["error"] = err
        try:
            _persist_run(
                topic_id=topic_id,
                flow_name=flow_name,
                status=status,
                started=started,
                finished=finished,
                payload=payload,
            )
        except Exception:
            logger.exception("topic_run persist failed for %s/%s", topic_id, flow_name)
            if status == "ok":
                raise
