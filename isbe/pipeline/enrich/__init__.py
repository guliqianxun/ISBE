"""Full text, method figures and evidence cards for the papers a report reviews
(src/enrich/). Entry point: `run`, called by the CLI and the generation queue.

What later stages read, all written here only:
- `fulltext (item_id, via, body, extracted_at, figure)`: `via` is `metrail` or
  `arxiv-html`; `body` the paper as Markdown; `figure` NULL while not looked
  for, `'{}'` when the paper has none, `{"caption", "file"}` (metrail, one
  image) or `{"caption", "files"}` (arXiv HTML, a group of panels), paths
  relative to the data directory under `figures/`.
- `cards (item_id, lang, key, card, created_at)`: see `cards`.
"""

from .calls import EnrichError
from .stage import run

__all__ = ["EnrichError", "run"]
