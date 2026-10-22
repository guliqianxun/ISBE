"""`/help`: the README and the bundled documents, with links kept between them."""

import re
from urllib.parse import unquote

from isbe.core import assets
from isbe.web import help_pages


def test_every_document_renders_with_the_navigation() -> None:
    for name, (path, _) in help_pages.DOCS.items():
        assert assets.path(*path.split("/")).is_file(), path
        html = help_pages.page(name).decode()
        assert html.startswith("<!doctype html>") and '<a href="/">' in html
        assert html.count('aria-current="page"') == 1
        assert f'<a href="{help_pages.href(name)}" aria-current="page">' in html
    assert help_pages.page("nope") is None


def test_links_between_documents_stay_in_help_and_land_on_a_heading() -> None:
    ids = {name: set(re.findall(r'<h\d id="([^"]+)"', help_pages.page(name).decode())) for name in help_pages.DOCS}
    seen = 0
    for name in help_pages.DOCS:
        html = help_pages.page(name).decode()
        assert "README.md" not in re.findall(r'href="([^"]*)"', html)
        for doc, fragment in re.findall(r'href="/help(?:\?doc=([a-z-]+))?(?:#([^"]*))?"', html):
            seen += 1
            target = doc or "readme"
            assert target in help_pages.DOCS, (name, doc)
            if fragment:
                assert unquote(fragment) in ids[target], (name, target, fragment)
    assert seen > 50


def test_paths_outside_the_documents_lose_their_link() -> None:
    link = help_pages._links("docs/configuration.md")
    assert link("../README.md") == "/help"
    assert link("source-interface.md#主题联网检索") == "/help?doc=source-interface#主题联网检索"
    assert link("../deploy/searxng/README.md") == "/help?doc=searxng"
    assert link("../examples/topics/nowcasting.yaml") is None
    assert link("../../etc/passwd") is None
    assert link("https://arxiv.org/x") == "https://arxiv.org/x"
    assert link("#docker") == "#docker"
