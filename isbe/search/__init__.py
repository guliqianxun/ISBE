"""Web search: search plans, the SearXNG provider, date rechecks and saved materials.

Bounded web discovery: search hints never become verified publication dates.
The interface (`list` is a builtin, so it is exported under that name rather
than defined with it):

- `SearchPlan`, `Provider`, `get(conn, id)`, `list(conn)`,
  `save(conn, plan, revision, id)`, `validate_saved(conn, plan) -> SearchPlan`
- `availability(conn)`, `preflight(conn, plan)`
- `snapshot(conn, run_id, plan)`, `run(conn, run_id, plan)`
- `validate_recheck(conn, plan_id, after)`, `snapshot_recheck(conn, run_id, plan_id, after)`,
  `recheck(conn, run_id)`

`capabilities` describes what a run through each provider or fixed-source
kind can enforce, for building requests from a research scope. `query`
parses the Google Scholar-style syntax of a plan's queries, writes the text sent
(with the plan's region and institution) and names the constraints a run leaves
to check in the results.

Errors the user can act on are `core.errors.HttpError`.
"""

from .plans import Provider, SearchPlan, get, save, validate_saved
from .plans import list_plans as list
from .providers import availability, preflight
from .rechecks import recheck, snapshot_recheck, validate_recheck
from .runner import run, snapshot

__all__ = [
    "Provider",
    "SearchPlan",
    "availability",
    "get",
    "list",
    "preflight",
    "recheck",
    "run",
    "save",
    "snapshot",
    "snapshot_recheck",
    "validate_recheck",
    "validate_saved",
]
