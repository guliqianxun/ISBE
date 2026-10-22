from isbe.collect.items import Item, squash, store
from isbe.core import db


def item(id: str, title: str, published_at: str) -> Item:
    return Item(id, "article", "rss:blog", title, None, "https://x", [], "en", published_at, {})


def test_store_counts_new_links_and_keeps_first_date() -> None:
    conn = db.connect(":memory:")
    a = item("rss:a", "A", "2026-10-10T00:00:00Z")
    assert store(conn, "t", [a, item("rss:a", "A", "2026-10-10T00:00:00Z")]) == 1
    assert store(conn, "t", [item("rss:a", "A v2", "2026-10-13T00:00:00Z")]) == 0
    assert store(conn, "u", [item("rss:a", "A v2", "2026-10-13T00:00:00Z")]) == 1
    row = conn.execute("SELECT title, published_at, authors, extra FROM items").fetchone()
    assert tuple(row) == ("A v2", "2026-10-10T00:00:00Z", "[]", "{}")


def test_json_columns_are_written_compactly() -> None:
    conn = db.connect(":memory:")
    paper = Item(
        "arxiv:1", "paper", "essaies", "T", "S", "u", ["A One", "张三"], "en",
        "2026-10-10T00:00:00Z", {"year": 2026, "categories": ["cs.LG"], "has_pdf": 1, "score": 1.0},
    )
    store(conn, "t", [paper])
    row = conn.execute("SELECT authors, extra FROM items").fetchone()
    assert row["authors"] == '["A One","张三"]'
    assert row["extra"] == '{"categories":["cs.LG"],"has_pdf":1,"score":1.0,"year":2026}'


def test_squash_uses_unicode_white_space() -> None:
    assert squash(" Radar\n  nowcasting　x y ") == "Radar nowcasting x y"
    # U+001C is whitespace to str.split() but not to Unicode White_Space, which squash follows.
    assert squash("a\x1cb") == "a\x1cb"
