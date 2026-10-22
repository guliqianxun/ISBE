"""Visible original article text: body selection, title, limits and omitted end tags."""

import pytest

from isbe.articles.text import MAX_DEPTH, MAX_HTML, MAX_PARAGRAPHS, MAX_TEXT, parse

QQ = "https://news.qq.com/rain/a/20261016A0DVE300"
NEWS = "https://example.com/news"


def paragraphs(html: str, url: str = NEWS) -> list[str]:
    return parse(html, url)[0]


def test_tencent_original_structure_keeps_paragraphs_and_inline_text() -> None:
    # Frozen structural excerpt from the public page; no search description is used.
    html = """<head><meta name="description" content="推动构建适配 ..."></head>
            <div class="content-article"><div id="article-content">
            <div class="comps-contentify-wrap comps-contentify-pc-wrap comps-contentify-arbitrary-wrap">
            <div class="rich_media_content"><p>推动构建适配<strong>国际金融中心定位</strong>的离岸金融体系。</p>
            <p>支持企业<a href="https://example.com">走出去</a>。</p></div></div></div></div>
            <div class="recommend"><p>推荐文章</p></div>"""
    assert paragraphs(html, QQ) == ["推动构建适配国际金融中心定位的离岸金融体系。", "支持企业走出去。"]
    with pytest.raises(ValueError):
        parse(html, "https://example.com/rain/a/1")


def test_semantic_body_is_plain_text_and_ignores_non_article_regions() -> None:
    html = """<div itemprop="articleBody"><p>A &amp; B &lt;script&gt;literal&lt;/script&gt;</p>
            <script>"<p>script text</p>"</script><style>.hidden{display:none}</style>
            <div hidden>hidden</div><div aria-hidden="true">hidden</div>
            <div style="DISPLAY: none !important">hidden</div><nav>navigation</nav>
            <form>form</form><div class="recommend-list">recommendation</div>
            <div id="comments">comment</div><!-- comment --><p onclick="bad()">Visible<img src="x" onerror="bad()"> text.</p>
            </div>"""
    assert paragraphs(html, "https://example.com/news/1") == [
        "A & B <script>literal</script>",
        "Visible text.",
    ]


PUBLISHERS = [
    ("https://www.cnr.cn/shanghai/tt/example.shtml", "<div class='article-content'>", "</div>"),
    ("https://m.jiemian.com/article/15183381.html", "<div class='article-content '>", "</div>"),
    ("https://www.sh.chinanews.com.cn/jinrong/example.shtml", "<div class='cms-news-article-content-blocknew'>", "</div>"),
    ("https://www.sohu.com/a/1086703335_122014422", "<article id='mp-editor' class='article'>", "</article>"),
    ("https://k.sina.com.cn/article_example.html", "<div id='article' class='article'>", "</div>"),
    ("https://news.sina.cn/2026-10-15/detail-example.html", "<section class='art_pic_card art_content'>", "</section>"),
    ("https://www.163.com/dy/article/example.html", "<div class='post_body'>", "</div>"),
    ("https://www.thepaper.cn/newsDetail_forward_34244360", "<div class='cententWrap__UojXm'>", "</div>"),
    ("https://www.prnasia.com/story/551681-1.shtml", "<div id='dvContent'>", "</div>"),
    ("https://bond.10jqka.com.cn/20261015/example.shtml", "<div class='news-content-parsed'>", "</div>"),
]  # fmt: skip


@pytest.mark.parametrize(("url", "open_", "close"), PUBLISHERS)
def test_verified_publisher_containers_keep_body_and_exclude_page_chrome(
    url: str, open_: str, close: str
) -> None:
    # Original-page container shapes; text is deliberately a short synthetic fixture.
    html = f"<nav>导航</nav>{open_}<p>机构<span>发布</span>年度数据。</p><p>报告期为2025年。</p>{close}<div>相关新闻</div>"
    assert paragraphs(html, url) == ["机构发布年度数据。", "报告期为2025年。"]
    for page, target, message in [
        (html, "https://example.com/article/1", "No unique"),
        (html + html, url, "No unique"),  # ambiguous
        (f"{open_}<p>没有闭合</p>", url, "incomplete"),
        (f"{open_}{close}", url, "empty"),
    ]:
        with pytest.raises(ValueError, match=message):
            parse(page, target)


def test_publisher_specific_non_article_text_is_not_presented_as_body() -> None:
    html = "<article id='mp-editor'><p>正文<a id='backsohucom'>返回搜狐，查看更多</a></p></article>"
    assert paragraphs(html, "https://www.sohu.com/a/1") == ["正文"]
    html = "<div class='article-content'><p>正文</p><div class='j_ads'>广告</div><iframe>视频备用页面</iframe><p>后续内容</p></div>"
    assert paragraphs(html, "https://m.jiemian.com/article/1.html") == ["正文", "后续内容"]
    html = "<div class='m-on f-pr'><h1>日期</h1><div id='aiBox'><template><p>AI内容</p></template></div><p><span>14:53</span>报道事实。<br><br>后续内容。</p><div id='jb_report'>举报</div></div>"
    assert paragraphs(html, "https://www.yicai.com/brief/1.html") == ["14:53报道事实。", "后续内容。"]
    with pytest.raises(ValueError):
        parse(html, "https://www.yicai.com/news/1.html")


def test_original_title_comes_from_page_metadata_and_is_bounded() -> None:
    html = "<head><title>页面标题</title><meta property='og:title' content='完整的 &amp; 原文标题'></head><div itemprop='articleBody'><p>正文</p></div>"
    assert parse(html, NEWS)[1] == "完整的 & 原文标题"
    html = "<head><title> 官方标题\n 来源 </title></head><div itemprop='articleBody'>正文</div>"
    assert parse(html, NEWS)[1] == "官方标题 来源"
    html = f"<head><title>{'x' * 2049}</title></head><div itemprop='articleBody'>正文</div>"
    assert parse(html, NEWS)[1] is None


@pytest.mark.parametrize(
    ("html", "url"),
    [
        ("<article><p>Unknown article card</p></article>", QQ),
        ("<div class='rich_media_content'><p>No article ancestor</p></div>", QQ),
        ("<div id='article-content'><div class='rich_media_content'></div></div>", QQ),
        ("<div id='article-content'><div class='rich_media_content'><p>Incomplete</p>", QQ),
        ("<div id='article-content'><div class='rich_media_content'><p>First</p></div><div class='rich_media_content'><p>Second</p></div></div>", QQ),
        ("<script>\"<div id='article-content'><div class='rich_media_content'>Fake</div></div>\"</script>", QQ),
        ("<div hidden><div id='article-content'><div class='rich_media_content'>Hidden</div></div></div>", QQ),
        ("<section><div itemprop='articleBody'><p>Premature ancestor close</p></section>", NEWS),
        ("<div itemprop='articleBody'>First</div><div itemprop='articleBody'>Second</div>", NEWS),
    ],
)  # fmt: skip
def test_rejects_missing_ambiguous_empty_or_incomplete_bodies(html: str, url: str) -> None:
    with pytest.raises(ValueError):
        parse(html, url)


@pytest.mark.parametrize(
    ("html", "message"),
    [
        ("x" * (MAX_HTML + 1), "HTML exceeds extraction limit"),
        (f"<div itemprop='articleBody'>{'x' * (MAX_TEXT + 1)}</div>", "exceeds extraction limits"),
        (f"<div itemprop='articleBody'>{'<p>text</p>' * (MAX_PARAGRAPHS + 1)}</div>", "exceeds extraction limits"),
        (f"<div itemprop='articleBody'>{'<div>' * MAX_DEPTH}text{'</div>' * MAX_DEPTH}</div>", "exceeds extraction limits"),
    ],
)  # fmt: skip
def test_refuses_extraction_overflow_without_returning_truncated_body(html: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse(html, NEWS)


def test_omitted_end_tags_do_not_hide_the_body() -> None:
    # Without </head> the body must not end up inside an open <head> and be treated as hidden.
    html = "<head><title>标题</title><meta charset='utf-8'><div itemprop='articleBody'><p>正文</p></div>"
    assert parse(html, NEWS) == (["正文"], "标题")
    # An unclosed <p hidden> or <li class='share'> ends at the next paragraph or item.
    html = "<div itemprop='articleBody'><p hidden>隐藏<p>第一段<p>第二段</div>"
    assert paragraphs(html) == ["第一段", "第二段"]
    html = "<div itemprop='articleBody'><ul><li class='share'>分享<li>要点</ul></div>"
    assert paragraphs(html) == ["要点"]


def test_svg_icon_title_is_not_the_article_title() -> None:
    html = (
        "<head><title>原文标题</title></head><div itemprop='articleBody'>"
        "<svg><title>分享图标</title><path/></svg><p>正文</p></div>"
    )
    assert parse(html, NEWS) == (["正文"], "原文标题")
    # A page without <title> takes none from an icon either.
    html = "<div itemprop='articleBody'><svg><title>图标</title></svg><p>正文</p></div>"
    assert parse(html, NEWS) == (["正文"], None)


def test_text_cap_counts_characters_not_utf8_bytes() -> None:
    # 400k Chinese characters are 1.2 MB of UTF-8 but fit the 1 MiB page a GBK read allows.
    filler = "<p hidden>" + "汉" * 400_000 + "</p>"
    html = f"<div itemprop='articleBody'>{filler}<p>正文</p></div>"
    assert len(html.encode("gbk")) <= MAX_HTML < len(html.encode("utf-8"))
    assert paragraphs(html) == ["正文"]
