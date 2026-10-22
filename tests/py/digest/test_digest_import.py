import base64
import json
import os
from pathlib import Path

import pytest

from isbe.core import db
from isbe.pipeline.digest import ReportTaken
from isbe.reports import importer


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def periods(conn: object) -> list[str]:
    return [r[0] for r in conn.execute("SELECT period FROM reports ORDER BY period")]  # type: ignore[attr-defined]


def test_import_imports_beside_own_reports_and_moves_figures(tmp_path: Path) -> None:
    src, data = tmp_path / "v1", tmp_path / "data"
    for p in ["nowcasting/2026-W40/.history", "nowcasting/2026-W41", "nowcasting/2026-W42", "nowcasting/odd"]:
        (src / p).mkdir(parents=True)
    # An older layout: no latest.md, the render named after its id.
    write(src / "nowcasting/2026-W40/2026-W40-ab12cd34.md", "# 短临 — 2026-W40\n")
    write(src / "nowcasting/2026-W40/.history/old.md", "# superseded\n")
    png = base64.b64encode(b"\x89PNG fake").decode()
    write(src / "nowcasting/2026-W41/latest.md", f"# 短临 — 2026-W41\n\n![framework](data:image/png;base64,{png})\n")
    write(src / "nowcasting/2026-W42/latest.md", "# 短临 — 2026-W42\n")
    write(src / "nowcasting/odd/latest.md", "x")
    data.mkdir()
    conn = db.connect(data / "isbe.db")
    conn.execute(
        "INSERT INTO reports VALUES ('nowcasting/2026-W42/weekly', 'nowcasting', '2026-W42', "
        "'weekly', 'reports/nowcasting/2026-W42.md', '{}', '2026-10-19')"
    )
    write(data / "reports/nowcasting/2026-W42.md", "# own\n")

    for _ in range(2):
        # a second run re-imports in place
        stats = db.job(conn, "import", None, lambda run_id, stats: importer.run(conn, data, str(src), stats))
        assert stats == {"imported": 3, "beside_own": 1, "figures": 1, "skipped": ["nowcasting/odd"]}
    assert (data / "reports/nowcasting/2026-W40.md").read_text() == "# 短临 — 2026-W40\n"
    assert periods(conn) == ["2026-W40", "2026-W41", "2026-W42", "2026-W42-v1"]
    md = (data / "reports/nowcasting/2026-W41.md").read_text()
    assert "](../../figures/v1-nowcasting-2026-W41-0.png)" in md, md
    assert (data / "figures/v1-nowcasting-2026-W41-0.png").read_bytes() == b"\x89PNG fake"
    assert (data / "reports/nowcasting/2026-W42.md").read_text() == "# own\n"
    assert (data / "reports/nowcasting/2026-W42-v1.md").read_text() == "# 短临 — 2026-W42\n"

    row = conn.execute("SELECT * FROM reports WHERE period = '2026-W40'").fetchone()
    file = src / "nowcasting/2026-W40/2026-W40-ab12cd34.md"
    assert (row["id"], row["kind"], row["path"]) == ("nowcasting/2026-W40/weekly", "weekly", "reports/nowcasting/2026-W40.md")
    assert row["fingerprint"] == json.dumps({"imported_from": str(file)}, ensure_ascii=False, separators=(",", ":"))
    ms = os.stat(file).st_mtime_ns // 1_000_000
    assert row["created_at"].endswith(f".{ms % 1000:03}Z") and len(row["created_at"]) == 24


def test_import_records_the_source_path_as_given(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    write(tmp_path / "v1/t/2026-10/latest.md", "# monthly\n")
    conn = db.connect(data / "isbe.db")
    stats: dict[str, object] = {}
    importer.run(conn, data, f"{tmp_path}//v1/", stats)
    assert stats == {"imported": 1, "beside_own": 0, "figures": 0}
    (kind, fingerprint) = conn.execute("SELECT kind, fingerprint FROM reports").fetchone()
    # The path is recorded as typed; only a trailing separator is not doubled.
    assert kind == "monthly" and json.loads(fingerprint) == {"imported_from": f"{tmp_path}//v1/t/2026-10/latest.md"}


def test_import_newest_report_and_figures_that_do_not_decode(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    old = write(tmp_path / "v1/t/2026-10-12/a.md", "old")
    new = write(tmp_path / "v1/t/2026-10-12/b.md", "![a](data:image/png;base64,QR==)![b](data:image/gif;base64,R0lG)"
                "![c](data:image/jpeg;base64,/9j) ![d](data:image/webp;base64, AAAA )")
    write(tmp_path / "v1/t/2026-10-12/.md", "dotfile")
    os.utime(old, ns=(1, 1_000_000_000))
    os.utime(new, ns=(1, 2_000_000_000))
    conn = db.connect(data / "isbe.db")
    stats: dict[str, object] = {}
    importer.run(conn, data, tmp_path / "v1", stats)
    assert stats == {"imported": 1, "beside_own": 0, "figures": 1}
    md = (data / "reports/t/2026-10-12.md").read_text()
    # Non-canonical padding, other types and bad base64 stay inline; outer spaces are trimmed.
    assert md == ("![a](data:image/png;base64,QR==)![b](data:image/gif;base64,R0lG)"
                  "![c](data:image/jpeg;base64,/9j) ![d](../../figures/v1-t-2026-10-12-0.webp)")
    assert (data / "figures/v1-t-2026-10-12-0.webp").read_bytes() == b"\x00\x00\x00"
    assert tuple(conn.execute("SELECT kind, created_at FROM reports").fetchone()) == ("daily", "1970-01-01T00:00:02.000Z")


def test_import_unreadable_source(tmp_path: Path) -> None:
    conn = db.connect(":memory:")
    with pytest.raises(importer.ImportFailed, match="^read "):
        importer.run(conn, tmp_path, tmp_path / "missing", {})


def test_import_refuses_a_path_another_report_owns(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    write(tmp_path / "v1/t/2026-W43/latest.md", "# imported\n")
    research = write(data / "reports/t/2026-W43.md", "# research, write-once\n")
    conn = db.connect(data / "isbe.db")
    conn.execute(
        "INSERT INTO reports VALUES ('t/2026-W43/research', 't', '2026-W43', 'research', "
        "'reports/t/2026-W43.md', '{}', 'x')"
    )
    with pytest.raises(ReportTaken, match="belongs to the research report t/2026-W43/research"):
        importer.run(conn, data, tmp_path / "v1", {})
    assert research.read_text() == "# research, write-once\n"
    assert periods(conn) == ["2026-W43"]
    # A file that no report row explains is not overwritten either.
    conn.execute("DELETE FROM reports")
    with pytest.raises(ReportTaken, match="exists but no report owns it"):
        importer.run(conn, data, tmp_path / "v1", {})
    assert research.read_text() == "# research, write-once\n" and periods(conn) == []
