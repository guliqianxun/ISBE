from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from isbe.facts.artifacts import TopicRun
from isbe.facts.db import make_session_factory


@dataclass
class _RunHandle:
    payload: dict = field(default_factory=dict)


@contextmanager
def topic_run(topic_id: str, flow_name: str):
    """Records a TopicRun row with start/finish/status.

    Yields a handle whose .payload dict is persisted to the row's payload column.
    On exception inside the block, status='failed' and error is recorded; exception re-raised.
    """
    started = datetime.now(timezone.utc)
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
        finished = datetime.now(timezone.utc)
        payload = dict(handle.payload)
        if err:
            payload["error"] = err
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
