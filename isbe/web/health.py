"""`GET /api/health`: whether the service can do its work right now, for an
operator, a monitor and the console (docs/question/api.md#health).

No sign-in is needed and nothing private is shown: whether the queue worker
thread is alive, and for each upstream whether it is configured and how its
latest request went (`isbe.core.upstreams`). `papers` is the paper source the
settings choose (`source`: essaies or the OpenAlex API, #121). It is probed when
nothing has been heard from it for `STALE` seconds: one request with
`sources.CHECK_TIMEOUT` (one probe at a time; a request that arrives during a
probe answers with what is known). The model is never called for this: its
state is that of the last real call, `unknown` until there is one.
"""

import threading
from typing import Any

from isbe.core import http, settings, upstreams
from isbe.question import openalex, sources

from .console import Console

# Seconds after which the paper source's last answer is old enough to ask again.
STALE = 60.0
_probing = threading.Lock()


def _client() -> http.Http:
    """The probe's HTTP client (tests replace it)."""
    return http.Http(sources.CHECK_TIMEOUT)


def health(console: Console) -> dict[str, Any]:
    values = settings.for_task(console.root)
    with settings.task_scope(values):
        source = settings.paper_source()
        essaies = settings.get("ESSAIES_URL")
        probe: sources.Source | None
        if source == "essaies":
            name, configured = upstreams.ESSAIES, bool(essaies)
            probe = sources.Essaies(_client(), essaies, retries=0) if essaies else None
        else:
            name, configured = upstreams.OPENALEX, True
            probe = openalex.OpenAlex(
                _client(), settings.get("OPENALEX_API_URL") or openalex.API, key=settings.get("OPENALEX_API_KEY"),
                mailto=settings.get("OPENALEX_MAILTO"), retries=0)
    if probe is not None:
        age = upstreams.age(name)
        if (age is None or age > STALE) and _probing.acquire(blocking=False):
            try:
                probe.check()
            except sources.SourceError:
                pass  # noted in upstreams by the client
            finally:
                _probing.release()
    model_ready = settings.snapshot(console.root)["llm"]["ready"] is True
    alive = getattr(console.generation, "worker_alive", None)
    out = {
        "worker": {"alive": alive is True},
        "upstreams": {
            "papers": upstreams.view(name, configured) | {"source": source},
            upstreams.MODEL: upstreams.view(upstreams.MODEL, model_ready),
        },
    }
    out["ok"] = alive is True and all(
        u["configured"] and u["state"] != "down" for u in out["upstreams"].values()
    )
    return out
