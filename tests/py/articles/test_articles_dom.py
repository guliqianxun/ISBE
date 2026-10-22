"""The HTML tree builder behind the article and date extractors."""

from isbe.articles import dom


def shape(node: dom.Element) -> list[object]:
    out: list[object] = []
    for child in node.children:
        if isinstance(child, str):
            out.append(child)
        else:
            prefix = f"{child.foreign}:" if child.foreign else ""
            mark = "" if child.closed or child.void else "~"
            out.append({f"{prefix}{child.name}{mark}": shape(child)})
    return out


def body(html: str) -> list[object]:
    document = dom.parse(html)
    found = [c for c in document.root.children if isinstance(c, dom.Element) and c.name == "body"]
    return shape(found[0]) if found else []


def test_optional_end_tags_close_like_a_browser() -> None:
    document = dom.parse("<head><title>T</title><meta charset=gbk><p>one<p>two<ul><li>a<li>b</ul>")
    assert shape(document.head) == [{"title": ["T"]}, {"meta": []}]
    assert body("<p>one<p>two<ul><li>a<li>b</ul>") == [
        {"p~": ["one"]},
        {"p~": ["two"]},
        {"ul": [{"li~": ["a"]}, {"li~": ["b"]}]},
    ]
    assert body("<p>a<div>b</div></p>") == [{"p~": ["a"]}, {"div": ["b"]}, {"p": []}]
    assert body("<dl><dt>t<dd>d</dl>") == [{"dl": [{"dt~": ["t"]}, {"dd~": ["d"]}]}]
    table = body("<table><tr><td>a<td>b<tr><td>c</table>x")
    assert table == [
        {"table": [{"tr~": [{"td~": ["a"]}, {"td~": ["b"]}]}, {"tr~": [{"td~": ["c"]}]}]},
        "x",
    ]


def test_explicit_and_implicit_closes_are_told_apart() -> None:
    assert body("<section><div>x</section>y") == [{"section": [{"div~": ["x"]}]}, "y"]
    assert body("<div><span>x</div>y</span>z") == [{"div": [{"span~": ["x"]}]}, "yz"]
    assert body("<div>open") == [{"div~": ["open"]}]
    # </body> does not end the body: later content still belongs to it.
    assert body("<body>a</body>b") == ["ab"]


def test_raw_text_and_foreign_content() -> None:
    assert body("<script/>var a='<p>x</p>';</script><p>y")[-1] == {"p~": ["y"]}
    svg = body("<svg><title>Icon</title><path/><style>a<b>c</b></style></svg><p>after")
    assert svg[0] == {"svg:svg~": [{"svg:title": ["Icon"]}, {"svg:path": []}, {"svg:style~": ["a"]}]}
    assert svg[1:] == [{"b": ["c"]}, {"p~": ["after"]}]
    assert body("<textarea><p>x</textarea>") == [{"textarea": ["<p>x"]}]
    head = dom.parse("<noscript><img src=x></noscript><meta name=after>").head
    assert shape(head) == [{"noscript": ["<img src=x>"]}, {"meta": []}]


def test_depth_is_bounded() -> None:
    # 128 open elements, html and body included, as in a page that spells them out.
    assert not dom.parse("<div>" * 126).overflow
    assert dom.parse("<div>" * 127).overflow
    assert not dom.parse("<span></span>" * 10_000).overflow
