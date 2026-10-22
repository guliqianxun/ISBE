"""Paper-topic collectors (arxiv, essaies, rss, github): candidates into `items`."""

from .items import CollectError, Item
from .run import Endpoints, collect

__all__ = ["CollectError", "Endpoints", "Item", "collect"]
