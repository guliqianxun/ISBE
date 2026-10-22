"""The web console: HTTP server, guards and the JSON API the SPA in web/ uses.

`serve(data, bind, shared, secure_cookies)` is `isbe serve`; `create_app`
builds the ASGI app for tests and embedding. See `server.py` for the queue
hook and limits, `generation_api.py` for the report-queue interface and
`routes_sources.py` for where the source registry's routes plug in.
"""

from .app import create_app
from .console import Limits
from .generation_api import Manager, ManagerFactory, UnavailableManager
from .guards import TrustedProxies
from .server import serve

__all__ = ["Limits", "Manager", "ManagerFactory", "TrustedProxies", "UnavailableManager", "create_app", "serve"]
