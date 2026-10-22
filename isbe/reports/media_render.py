"""The media-week draft's Markdown.

Every collected candidate appears exactly once, grouped by its publication day
against the report week and the search's collection window. Search snippets
stay labelled as such and every text is escaped, so a title or summary cannot
add links, images or HTML to the report.
"""

import enum
from dataclasses import dataclass
from typing import Any

from isbe.core import validators
from isbe.reports.media_dates import publication, show

_SPACES = str.maketrans({"\n": " ", "\r": " ", "\t": " "})
_ESCAPES = {"<": "&lt;", ">": "&gt;", "&": "&amp;"} | {c: "\\" + c for c in "\\`*_[]{}#|"}

# Publisher names for the hosts that the saved searches return most often.
_SOURCES = {
    "cnr.cn": "央广网",
    "cnfin.com": "中国金融信息网",
    "finance.eastmoney.com": "东方财富网",
    "21jingji.com": "21财经",
    "m.21jingji.com": "21财经",
    "jiemian.com": "界面新闻",
    "m.jiemian.com": "界面新闻",
    "sh.chinanews.com.cn": "中新网上海",
    "thepaper.cn": "澎湃新闻",
    "yicai.com": "第一财经",
    "news.qq.com": "腾讯新闻",
    "view.inews.qq.com": "腾讯新闻",
    "news.sina.cn": "新浪新闻",
    "k.sina.com.cn": "新浪新闻",
    "sohu.com": "搜狐",
    "163.com": "网易",
    "prnasia.com": "美通社",
    "bond.10jqka.com.cn": "同花顺",
}


class Group(enum.Enum):
    WEEK = "weekly"
    BACKGROUND = "background"
    NEWER = "newer"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"
    OUTSIDE = "outside"


@dataclass(frozen=True)
class Window:
    """The report week and the saved search's collection window, as day numbers."""

    start: int
    end: int
    collection_start: int
    collection_end: int


# (group, heading, folded): the report's sections in order; the uncertain ones fold.
_GROUPS = (
    (Group.WEEK, "本周报道", False),
    (Group.BACKGROUND, "本周之前 · 采集窗口回看", False),
    (Group.NEWER, "本周之后 · 新增报道", False),
    (Group.UNKNOWN, "日期待核对", True),
    (Group.CONFLICT, "日期冲突", True),
    (Group.OUTSIDE, "采集窗口外材料", True),
)


def get(value: Any, *keys: str) -> Any:
    """`value[k1][k2]...`, None for anything missing."""
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def md(value: str) -> str:
    """Inline text that Markdown shows literally (media's escape set, not the briefing's)."""
    return "".join(_ESCAPES.get(c, c) for c in value.translate(_SPACES))


def source(host: str) -> str:
    """The publisher's name for `host`, else the host itself."""
    return _SOURCES.get(host.removeprefix("www."), host)


def group(item: Any, window: Window) -> Group:
    status = get(item, "content", "date_status")
    if status == "conflict":
        return Group.CONFLICT
    if not isinstance(status, str) or status not in ("in_range", "out_of_range"):
        return Group.UNKNOWN
    day = publication(item)
    if day is None:
        return Group.UNKNOWN
    if window.start <= day <= window.end:
        return Group.WEEK
    if window.collection_start <= day <= window.collection_end:
        return Group.BACKGROUND if day < window.start else Group.NEWER
    return Group.OUTSIDE


def _link_url(raw: str) -> tuple[str, str] | None:
    """(host, serialized URL) of an HTTP(S) URL with a host and no credentials."""
    try:
        url = validators.whatwg(raw)
    except ValueError:
        return None
    if url.protocol not in ("http:", "https:") or not url.hostname or url.username or url.password:
        return None
    return url.hostname, url.href


def entry(item: Any, selected: Group) -> str:
    content = get(item, "content")
    title = get(content, "title")
    title = title if isinstance(title, str) else "无标题材料"
    raw = get(item, "url")
    url = _link_url(raw if isinstance(raw, str) else "")
    if url is None:
        link = md(title)
    else:
        host, href = url
        href = href.replace("<", "%3C").replace(">", "%3E")
        link = f"[{md(source(host))}｜{md(title)}](<{href}>)"
    if selected is Group.UNKNOWN:
        when = "日期待核对"
    elif selected is Group.CONFLICT:
        when = "日期冲突"
    else:
        day = publication(item)
        when = "日期待核对" if day is None else show(day)
    summary = get(content, "summary")
    out = f"### {when} · {link}\n\n"
    if isinstance(summary, str) and validators.trim(summary):
        out += f"**检索摘要：** {md(summary)}\n\n"
    else:
        out += "检索未返回摘要，可打开原文查看。\n\n"
    return out


def _sorted(subset: list[Any]) -> list[Any]:
    """Newest publication first (undated last), then by id, keeping ties in order."""

    def by_id(item: Any) -> tuple[int, str]:
        id = get(item, "id")
        return (1, id) if isinstance(id, str) else (0, "")

    def by_day(item: Any) -> tuple[int, int]:
        day = publication(item)
        return (0, 0) if day is None else (1, day)

    # Two stable passes equal one stable sort with the combined comparator.
    return sorted(sorted(subset, key=by_id), key=by_day, reverse=True)


def render(snapshot: dict[str, Any], window: Window) -> tuple[str, dict[str, int]]:
    """The report's Markdown and its per-group counts (`weekly`, `background`, ...)."""
    items: list[Any] = snapshot["materials"]
    title = get(snapshot, "plan", "config", "topic")
    if not isinstance(title, str) or not title:
        title = "媒体资料"
    grouped = {selected: [item for item in items if group(item, window) is selected]
               for selected, _, _ in _GROUPS}
    counts = {selected.value: len(grouped[selected]) for selected, _, _ in _GROUPS}
    out = [
        f"# {md(title)}｜媒体周报\n\n"
        f"> 覆盖窗口：{show(window.start)} → {show(window.end)}（周一至周日）\n\n"
        f"本期收录 {counts['weekly']} 条发布日期在本周的候选报道，按日期整理，尚未经人工选稿。\n\n"
        "检索摘要可能截断或混入页面其他内容，请以链接原文为准。事件发生日期可能与报道发布日期不同。\n\n"
        "## TL;DR\n\n"
        f"- 本周：{counts['weekly']} 条候选报道。\n"
        f"- 检索回看窗口：{show(window.collection_start)} → {show(window.collection_end)}；"
        f"周前回看 {counts['background']} 条，周后新增 {counts['newer']} 条。\n"
        f"- 日期待核对 {counts['unknown']} 条、日期冲突 {counts['conflict']} 条，保留在文末供审阅。\n\n"
    ]
    for selected, heading, folded in _GROUPS:
        out.append(f"## {heading}（{counts[selected.value]}）\n\n")
        subset = _sorted(grouped[selected])
        if not subset:
            out.append("本期暂无材料。\n\n")
            continue
        if folded:
            out.append("<details>\n<summary>展开完整材料</summary>\n\n")
        out.extend(entry(item, selected) for item in subset)
        if folded:
            out.append("</details>\n\n")
    out.append("---\n\n本期为冻结版本，后续补充日期或重新检索会生成新版本。相同事件的不同报道仍分别保留。\n")
    return "".join(out), counts
