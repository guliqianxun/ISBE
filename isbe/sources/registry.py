"""The source registry: configured sources, their
collected materials and the durable operations that collect them.

The implementation is split by responsibility; this module is the interface
the web routes and the CLI use (`list` and `import_` stand in for the names
`list` and `import`, a builtin and a keyword):

- `error(code, message) -> HttpError`, `status(e) -> int`
- `Source`, `get(conn, id)`, `list(conn)`, `save(conn, source, revision, id)`
- `export(conn)`, `import_(conn, sources)`, `catalog(conn, CatalogQuery(...))`,
  `SourceRevision`, `batch(conn, sources, enabled)`
- `materials_filtered(conn, source, offset, date_status)`, `timeline(conn, material_id)`
- `Collect`, `Discover`, `Search`, `Recheck` (the `Operation`s),
  `start(data, operation, request_id)`, `job(conn, id)`, `jobs(conn)`, `recover(data)`
"""

from .registry_catalog import CatalogQuery, SourceRevision, batch, catalog, export
from .registry_catalog import import_sources as import_
from .registry_config import Source, error, get, save, status
from .registry_config import list_sources as list
from .registry_discover import discover
from .registry_jobs import (
    Collect,
    Discover,
    Operation,
    Recheck,
    Search,
    job,
    jobs,
    recover,
    start,
)
from .registry_materials import collect, materials_filtered, record
from .registry_timeline import timeline

__all__ = [
    "CatalogQuery",
    "Collect",
    "Discover",
    "Operation",
    "Recheck",
    "Search",
    "Source",
    "SourceRevision",
    "batch",
    "catalog",
    "collect",
    "discover",
    "error",
    "export",
    "get",
    "import_",
    "job",
    "jobs",
    "list",
    "materials_filtered",
    "record",
    "recover",
    "save",
    "start",
    "status",
    "timeline",
]
