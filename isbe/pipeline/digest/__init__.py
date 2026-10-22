"""The digest stage: the period's report from kept papers, tracked repos and memory.

`run(conn, llm, run_id, topic, data, stats)` is the stage the CLI `digest`
command and the generation queue call inside `db.job(conn, "digest", topic.id, ...)`.
It reads `items`, `triage`, `fulltext`, `cards` with plain SQL, writes
`reports/<topic>/<period>.md` and its `reports` row, and proposes memory
drafts. `ReportTaken` means the period's file belongs to another report kind.
"""

from .run import DigestError, run
from .store import ReportTaken

__all__ = ["DigestError", "ReportTaken", "run"]
