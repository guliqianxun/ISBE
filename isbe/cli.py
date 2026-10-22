"""Command-line front end.

Output lines are stable: scripts read them.
Feature packages are imported by the command that needs them, so `init`,
`topics` and `account-add` start without loading the pipeline.
"""

import argparse
import os
import sys
from collections.abc import Callable, Iterable, Sequence
from contextlib import closing, nullcontext
from pathlib import Path
from typing import Any

from isbe import __version__
from isbe.core import db, jsonfmt, locks, settings
from isbe.core.errors import describe
from isbe.topics import Topic, load_dir
from isbe.topics.model import debug

# Commands that write pipeline state hold the execution lock for their whole run.
LOCKED = frozenset(
    {"collect", "triage", "enrich", "digest", "import-reports", "research", "media-weekly", "media-briefing"}
)

type Handler = Callable[[argparse.Namespace, Path, str], None]


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="isbe", description="ISBE — self-hosted research radar")
    ap.add_argument("-V", "--version", action="version", version=f"isbe {__version__}")
    data_help = "Data directory holding topics/, memory/, reports/ and isbe.db."
    ap.add_argument("--data", default=os.environ.get("ISBE_DATA") or "data", help=data_help)
    # clap's `global = true`: --data is accepted after the subcommand as well.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data", default=argparse.SUPPRESS, help=data_help)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def command(name: str, help: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help, description=help, parents=[common])

    def topics(p: argparse.ArgumentParser) -> None:
        p.add_argument("topics", nargs="*", metavar="TOPICS")

    command("init", "Create the data directory layout and the database (safe to re-run).")
    command("topics", "Validate topics/*.yaml and *.yml and list them.")
    sources = command("sources", "Configure and collect RSS and website sources without model calls.")
    from isbe.sources import cli as sources_cli

    sources_cli.add_arguments(sources)
    p = command("collect", "Fetch new items for the given topics (default: every active topic).")
    topics(p)
    p.add_argument("--essaies-url", help="essaies base URL. Without it, papers come from the arXiv API.")
    p.add_argument("--arxiv-api")
    p.add_argument("--github-api")
    topics(command("triage", "Judge the window's items for the given topics (default: every active topic)."))
    p = command(
        "enrich",
        "Full text (via `METRAIL_API_URL`) and evidence cards for the papers the next report will review.",
    )
    topics(p)
    p.add_argument("--essaies-url", help="essaies base URL, where the PDFs come from. Without it, from arxiv.org.")
    p = command(
        "import-reports",
        "Import reports written elsewhere, laid out as <dir>/<topic>/<period>/latest.md (the Python "
        "version's artifacts/). A period with an ISBE report lands beside it as <period>-v1.",
    )
    # Kept as typed: `imported_from` records the path the way the user wrote it.
    p.add_argument("dir")
    p = command("research", "Generate a research report from a fixed source manifest and preserve its evidence.")
    p.add_argument("input", type=Path)
    p = command(
        "media-weekly",
        "Organize saved web-search candidates into an immutable weekly media draft, without a model.",
    )
    p.add_argument("plan_id")
    p.add_argument("--topic", required=True)
    p.add_argument("--week-end", required=True, help="The completed week's Sunday, in YYYY-MM-DD format.")
    p = command(
        "media-briefing", "Publish an editor-prepared factual briefing from a media report and saved original text."
    )
    p.add_argument("input", type=Path)
    p.add_argument("--topic", required=True)
    p.add_argument("--source-period", required=True)
    p = command("serve", "Serve the web console, service settings and topic manager.")
    p.add_argument(
        "--bind",
        default=os.environ.get("ISBE_BIND") or "127.0.0.1:8080",
        help="Address to listen on; keep it on localhost unless a proxy guards it.",
    )
    p.add_argument("--shared", action="store_true", help="Require login and isolate each user workspace.")
    p.add_argument("--secure-cookies", action="store_true", help="Set Secure session cookies when served behind HTTPS.")
    p = command("account-add", "Create an account; use --admin to bootstrap shared mode.")
    p.add_argument("username")
    p.add_argument("--password-file", required=True, type=Path)
    p.add_argument("--admin", action="store_true")
    p = command(
        "digest",
        "Triage, enrich, then write the period's report for the given topics (default: every active topic).",
    )
    topics(p)
    p.add_argument("--essaies-url")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        run(args)
    except Exception as error:
        print(describe_error(error), file=sys.stderr)
        return 1
    return 0


def run(args: argparse.Namespace) -> None:
    raw: str = args.data
    data = Path(raw)
    settings.init(data)
    with locks.execution_lock(data) if args.cmd in LOCKED else nullcontext():
        COMMANDS[args.cmd](args, data, raw)


def _init(args: argparse.Namespace, data: Path, raw: str) -> None:
    for sub in ("topics", "memory", "reports"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    db.connect(data / "isbe.db").close()
    # The path is printed as given, trailing slash and all.
    print(f"ready: {raw}")


def _topics(args: argparse.Namespace, data: Path, raw: str) -> None:
    for t in load_dir(data / "topics"):
        s = t.schedules
        print(
            f"{t.id:<22} {t.flavor().value:<8} {t.cadence.value:<8} {t.lang.value:<6} "
            f"collect={s.collect or '-':<14} digest={s.digest or '-':<12} {t.label}"
            f"{'' if t.active else '  (inactive)'}"
        )


def _sources(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe.sources import cli as sources_cli

    sources_cli.run(data, args)


def _collect(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe.collect import Endpoints, collect

    selected = select(load_dir(data / "topics"), args.topics)
    endpoints = Endpoints(
        essaies=configured(args.essaies_url, "ESSAIES_URL"),
        arxiv_api=configured(args.arxiv_api, "ARXIV_API_URL") or settings.ARXIV_API,
        github_api=configured(args.github_api, "GITHUB_API_URL") or settings.GITHUB_API,
    )
    with _open(data) as conn:
        each(selected, lambda t: job(conn, t, "collect", lambda _, stats: collect(conn, t, endpoints, stats)))


def _triage(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe.core.llm import Llm
    from isbe.pipeline import triage

    selected = select(load_dir(data / "topics"), args.topics)
    with _open(data) as conn:
        llm = Llm.from_env()
        each(selected, lambda t: job(conn, t, "triage", lambda run, stats: triage.run(conn, llm, run, t, stats)))


def _enrich(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe.core.llm import Llm
    from isbe.pipeline import enrich

    selected = select(load_dir(data / "topics"), args.topics)
    essaies = configured(args.essaies_url, "ESSAIES_URL")
    with _open(data) as conn:
        llm = Llm.from_env()
        each(
            selected,
            lambda t: job(conn, t, "enrich", lambda run, stats: enrich.run(conn, llm, run, t, data, essaies, stats)),
        )


def _digest(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe.core.llm import Llm
    from isbe.pipeline import digest, enrich, triage

    selected = select(load_dir(data / "topics"), args.topics)
    essaies = configured(args.essaies_url, "ESSAIES_URL")
    with _open(data) as conn:
        llm = Llm.from_env()

        def one(t: Topic) -> bool:
            # A failed triage still leaves every unjudged item kept, and a failed
            # enrich leaves those papers on their abstracts, so the report goes on.
            job(conn, t, "triage", lambda run, stats: triage.run(conn, llm, run, t, stats))
            job(conn, t, "enrich", lambda run, stats: enrich.run(conn, llm, run, t, data, essaies, stats))
            return job(conn, t, "digest", lambda run, stats: digest.run(conn, llm, run, t, data, stats))

        each(selected, one)


def _import_reports(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe.reports import importer

    with _open(data) as conn:
        stats = db.job(conn, "import", None, lambda _, stats: importer.run(conn, data, args.dir, stats))
    print(f"import ok {jsonfmt.dumps(stats)}")


def _research(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe.core.llm import Llm
    from isbe.reports import research

    with _open(data) as conn:
        llm = Llm.from_env()
        stats = db.job(conn, "research", None, lambda run, stats: research.run(conn, llm, run, data, args.input, stats))
    print(f"research ok {jsonfmt.dumps(stats)}")


def _media_weekly(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe.reports import media

    with _open(data) as conn:

        def fill(_: str, stats: dict[str, Any]) -> None:
            value = media.run(conn, data, args.plan_id, args.topic, args.week_end)
            if isinstance(value, dict):
                stats.update(value)

        stats = db.job(conn, "media-weekly", args.topic, fill)
    print(f"media weekly ok {jsonfmt.dumps(stats)}")


def _media_briefing(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe.reports import media

    with _open(data) as conn:

        def fill(_: str, stats: dict[str, Any]) -> None:
            stats.update(media.create_briefing(conn, data, args.topic, args.source_period, args.input))

        stats = db.job(conn, "media-briefing", args.topic, fill)
    print(f"media briefing ok {jsonfmt.dumps(stats)}")


def _serve(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe.generation import Manager
    from isbe.reports import media
    from isbe.web import serve
    from isbe.web.routes_sources import api

    # Opening the queue starts its single worker; serve closes it on shutdown.
    serve(
        data,
        args.bind,
        args.shared,
        args.secure_cookies,
        generation=Manager.open,
        articles=media.articles,
        sources=api,
    )


def _account_add(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe import auth

    data.mkdir(parents=True, exist_ok=True)
    with open(args.password_file, "rb") as file:
        password = file.read(2048).decode("utf-8")
    with _open(data) as conn:
        user = auth.create_user(conn, args.username, password.rstrip("\r\n"), args.admin)
    print(f"Account created: {user.username} ({user.role})")


COMMANDS: dict[str, Handler] = {
    "init": _init,
    "topics": _topics,
    "sources": _sources,
    "collect": _collect,
    "triage": _triage,
    "enrich": _enrich,
    "digest": _digest,
    "import-reports": _import_reports,
    "research": _research,
    "media-weekly": _media_weekly,
    "media-briefing": _media_briefing,
    "serve": _serve,
    "account-add": _account_add,
}


def _open(data: Path) -> Any:
    return closing(db.connect(data / "isbe.db"))


def configured(value: str | None, name: str) -> str | None:
    """An explicit CLI value wins, even an empty one (which means "none")."""
    if value is not None:
        return value if value.strip() else None
    return settings.get(name)


def each(topics: Iterable[Topic], f: Callable[[Topic], bool]) -> None:
    """Run `f` per topic; every topic runs even if an earlier one failed."""
    topics = list(topics)
    failed = sum(not f(t) for t in topics)
    if failed:
        raise RuntimeError(f"{failed} of {len(topics)} topics failed")


def job(conn: Any, t: Topic, name: str, fn: Callable[[str, dict[str, Any]], None]) -> bool:
    """Run one ledger job for `t` and print its outcome; true if it succeeded."""
    try:
        stats = db.job(conn, name, t.id, fn)
    except Exception as error:
        print(f"{t.id:<22} {name:<8} failed  {describe(error)}", file=sys.stderr)
        return False
    print(f"{t.id:<22} {name:<8} ok      {jsonfmt.dumps(stats)}")
    return True


def select(topics: list[Topic], names: Sequence[str]) -> list[Topic]:
    """The named topics, or every active one when none are named."""
    ids = {t.id for t in topics}
    for name in names:
        if name not in ids:
            raise ValueError(f"no topic {debug(name)} in topics/")
    return [t for t in topics if (t.id in names if names else t.active)]


def describe_error(error: BaseException) -> str:
    """The error as the CLI prints it: `Error: <message>`, then the causes under `Caused by:`."""
    chain: list[str] = []
    current: BaseException | None = error
    while current is not None and len(chain) < 64:
        text = str(current) or type(current).__name__
        if not chain or chain[-1] != text:
            chain.append(text)
        current = current.__cause__
    lines = [f"Error: {chain[0]}"]
    if len(chain) == 2:
        lines += ["", "Caused by:", f"    {chain[1]}"]
    elif len(chain) > 2:
        lines += ["", "Caused by:", *(f"    {i}: {c}" for i, c in enumerate(chain[1:]))]
    return "\n".join(lines)
