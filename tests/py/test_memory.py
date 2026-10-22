from pathlib import Path

import pytest

from isbe import memory
from isbe.topics import yamlio


def note(root: Path, rel: str, name: str, kind: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ntype: {kind}\nrevision: 3\n---\nbody of {name}\n")


def test_loads_this_topics_notes_and_drafts_only_into_pending(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    note(root, "topics/nowcasting.theses.md", "nowcasting.theses", "topic")
    note(root, "topics/video.md", "video", "topic")
    note(root, "feedback/style.md", "style", "feedback")
    note(root, "reading/2026/W40/x.md", "x", "reading")
    note(root, ".pending/topics/nowcasting.md", "nowcasting", "topic")
    (root / "topics/broken.md").write_text("no frontmatter")

    notes, skipped = memory.load(root, "nowcasting")
    assert [(n.name, n.revision) for n in notes] == [("style", 3), ("nowcasting.theses", 3)]
    assert notes[1].body == "body of nowcasting.theses"
    assert len(skipped) == 1 and skipped[0] == f"{root / 'topics/broken.md'}: no --- frontmatter"

    a = memory.write_draft(root, "topics/nowcasting.theses.md", "new thesis", "digest nowcasting 2026-W41", "2026-10-13")
    b = memory.write_draft(root, "topics/nowcasting.theses.md", "another", "o", "2026-10-13")
    assert a == root / ".pending/topics/nowcasting.theses.md"
    assert b == root / ".pending/topics/nowcasting.theses-2.md"
    assert memory.write_draft(root, "topics/nowcasting.theses.md", "another", "o", "2026-10-13") == b
    assert 'type: "topic"\n' in a.read_text()
    assert memory.read_note(a).kind == "topic"
    for bad in ["../x.md", "topics/../../x.md", "/etc/x.md", "notes/x.md", "topics/x.txt", "topics\\..\\x.md", "./topics/x.md", ""]:
        with pytest.raises(ValueError):
            memory.write_draft(root, bad, "c", "o", "d")
    assert memory.pending(root) == ["topics/nowcasting.md", "topics/nowcasting.theses-2.md", "topics/nowcasting.theses.md"]
    assert memory.load(root, "nowcasting")[0] == notes, "drafts never enter the live tree"


def test_missing_memory_directory_has_no_notes(tmp_path: Path) -> None:
    assert memory.load(tmp_path / "memory", "t") == ([], [])
    assert memory.pending(tmp_path / "memory") == []


def test_notes_without_a_body_crlf_and_defaults(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("---\r\nname: a\r\ntype: user\r\n---")
    (tmp_path / "b.md").write_text("---\nname: b\ntype: feedback\ndescription: 'free text: with colons'\n---\n\n  body  \n")
    assert memory.read_note(tmp_path / "a.md") == memory.Note(name="a", kind="user", revision=1, body="")
    assert memory.read_note(tmp_path / "b.md").body == "body"
    (tmp_path / "c.md").write_text("---\nname: c\ntype: topic\nname: d\n---\n")
    with pytest.raises(ValueError, match="duplicate mapping key: name"):
        memory.read_note(tmp_path / "c.md")


@pytest.mark.parametrize(
    "stem",
    [
        "nowcasting.theses", "a: b", "yes", "2026-10-16", "123", "#x", "- x", "'q'", '"q"', "中文 笔记", "x\ty", "[x]",
        "&a", "*a", "!x", "%x", "@x", "---", "~", "null", "a #b", " lead", "trail ",
    ],
)
def test_draft_frontmatter_round_trips_any_origin_and_stem(tmp_path: Path, stem: str) -> None:
    origin = "digest draft: nowcasting 2026-W42"
    path = memory.write_draft(tmp_path, f"topics/{stem}.md", "content: with a colon\n---\nand a rule", origin, "2026-10-13")
    parsed = memory.read_note(path)
    assert (parsed.name, parsed.kind, parsed.revision) == (stem, "topic", 1)
    assert parsed.body == "content: with a colon\n---\nand a rule"
    front = path.read_text().split("\n---\n", 1)[0].removeprefix("---\n")
    assert yamlio.load_value(front) == {
        "name": stem, "description": origin, "type": "topic", "created": "2026-10-13",
        "updated": "2026-10-13", "source": "agent-inferred", "revision": 1,
    }
    for odd in ["origin\nwith: newline", "\N{LINE SEPARATOR}sep", "quote \" and \\ slash", "x" * 500]:
        again = memory.write_draft(tmp_path, f"feedback/{stem}.md", "c", odd, "2026-10-13")
        assert memory.read_note(again).name == stem


def test_drafts_never_overwrite_what_is_already_there(tmp_path: Path) -> None:
    target = tmp_path / ".pending/user/prefs.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\xff not utf-8")
    path = memory.write_draft(tmp_path, "user/prefs.md", "c", "o", "2026-10-13")
    assert path.name == "prefs-2.md"
    assert target.read_bytes() == b"\xff not utf-8"
    (tmp_path / ".pending/user/prefs-3.md").mkdir()
    assert memory.write_draft(tmp_path, "user/prefs.md", "different", "o", "2026-10-13").name == "prefs-4.md"
    assert not [p for p in target.parent.iterdir() if p.name.endswith(".tmp")]
    assert memory.write_draft(tmp_path, "reference/a/./b.md", "c", "o", "d") == tmp_path / ".pending/reference/a/b.md"


def test_the_old_unquoted_frontmatter_was_unreadable(tmp_path: Path) -> None:
    # Unquoted front matter as older builds wrote it for a digest draft; shows why every value is quoted now.
    old = "---\nname: x\ndescription: digest draft: nowcasting 2026-W42\ntype: topic\nrevision: 1\n---\nc\n"
    (tmp_path / "x.md").write_text(old)
    with pytest.raises(ValueError, match="mapping values are not allowed"):
        memory.read_note(tmp_path / "x.md")
