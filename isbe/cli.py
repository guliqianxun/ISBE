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
from isbe.core import db, jsonfmt, locks, logs, settings
from isbe.core.errors import describe
from isbe.question import compose
from isbe.topics import Topic, load_dir
from isbe.topics.model import debug

# Commands that write pipeline state hold the execution lock for their whole run.
LOCKED = frozenset(
    {
        "collect", "triage", "enrich", "digest", "import-reports", "research", "media-weekly", "media-briefing",
        "question", "question-citations", "question-survey",
    }
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
    p = command(
        "question",
        "Research a question: plan, search essaies and screen the candidates; the run shows in the console too.",
    )
    p.add_argument("question")
    p.add_argument("--lang", choices=("zh", "en"), default="zh", help="The language of the question.")
    p.add_argument("--from", dest="year_from", type=int, help="The earliest publication year.")
    p.add_argument("--to", dest="year_to", type=int, help="The latest publication year.")
    p.add_argument("--engine", choices=compose.QUESTION_ENGINES, help=f"The search engine (default: {compose.DEFAULT}).")
    p.add_argument("--review", action="store_true", help="Wait until the plan is started (or edited) in the console.")
    p.add_argument("--no-expand", dest="expand", action="store_false",
                   help="Do not add one hop of citations from the included papers.")
    p.add_argument("--expand-per-paper", type=int, help="Papers the expansion adds per included paper (default: 20).")
    p.add_argument("--expand-total", type=int, help="Papers the expansion adds per run (default: 200).")
    p.add_argument("--citations-full", type=int,
                   help="Kept papers looked up through OpenAlex for their citations (default: 300; 0: none).")
    p.add_argument("--essaies-url", help="Search this essaies whatever ISBE_PAPER_SOURCE says (default: the "
                   "paper source of the settings).")
    p = command(
        "question-citations",
        "Look up the citations between a question run's candidates now (a run that finished before they were "
        "fetched); the console's star map draws them.",
    )
    p.add_argument("run_id")
    p.add_argument("--full", type=int, help="Kept papers to look up through OpenAlex (default: 300, at most 50 with the OpenAlex API and "
                   "no OPENALEX_API_KEY; 0: archive only).")
    p.add_argument("--essaies-url", help="Look up in this essaies whatever ISBE_PAPER_SOURCE says (default: the "
                   "paper source of the settings).")
    p = command(
        "question-graph",
        "Build a question run's knowledge graph now (a run that finished before it was built, or after "
        "overrides); prints its communities and most central papers.",
    )
    p.add_argument("run_id")
    p.add_argument("--top", type=int, default=10, help="Communities and papers to print (default: 10).")
    p = command(
        "question-survey",
        "Write a survey report from a question run's included papers: full text and verified facts where "
        "arXiv has them, abstracts otherwise; the report goes on the shelf.",
    )
    p.add_argument("run_id")
    p.add_argument("--max-papers", type=int, help="Included papers the survey reviews, most relevant first (default: 20).")
    p.add_argument("--no-fulltext", dest="fulltext", action="store_false",
                   help="Fetch no new full text; use what is stored and the abstracts.")
    p = command("serve", "Serve the web console, service settings and topic manager.")
    p.add_argument(
        "--bind",
        default=os.environ.get("ISBE_BIND") or "127.0.0.1:8080",
        help="Address to listen on; keep it on localhost unless a proxy guards it.",
    )
    p.add_argument("--shared", action="store_true", help="Require login and isolate each user workspace.")
    p.add_argument("--secure-cookies", action="store_true", help="Set Secure session cookies when served behind HTTPS.")
    p = command(
        "settings",
        "Import or export the service settings as a JSON file (format in docs/settings.md); "
        "import checks every value before saving any.",
    )
    p.add_argument("action", choices=("import", "export"))
    p.add_argument("file", nargs="?", type=Path, help="The file to import (import only).")
    p.add_argument("--secrets", action="store_true", help="Include API keys, tokens and passwords (export only).")
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
    logs.setup()
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


def _question(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe.core import http
    from isbe.question import flow, service, sources

    body = {"question": args.question, "lang": args.lang, "year_from": args.year_from, "year_to": args.year_to,
            "engine": args.engine, "review": args.review, "expand": args.expand,
            "expand_per_paper": args.expand_per_paper, "expand_total": args.expand_total,
            "citations_full": args.citations_full}
    with _open(data) as conn:
        run, _ = service.ask(data, conn, "local", {k: v for k, v in body.items() if v is not None})
    run_id = run["run_id"]
    print(f"question run {run_id}", flush=True)
    services = flow.Services()
    if args.essaies_url:
        url = args.essaies_url
        services.source = lambda engine: sources.Essaies(http.Http(sources.TIMEOUT), url, engine=engine)
    printer = _QuestionPrinter(data, run_id)
    printer.start()
    try:
        state = flow.run_through(data, run_id, services)
    finally:
        printer.stop()
    with _open(data) as conn:
        run = service.summary(data, conn, run_id)
    print(f"question {run['status']} {jsonfmt.dumps({'run_id': run_id, **run['counts'], 'problems': run['problems']})}")
    if state == "paused":
        print("paused: resume it in the console", file=sys.stderr)
    elif run["status"] == "failed":
        raise RuntimeError(f"question run {run_id} failed; see its events in the console")


def _question_citations(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe.core import http
    from isbe.question import citations, runs, sources

    source = (sources.Essaies(http.Http(sources.TIMEOUT), args.essaies_url) if args.essaies_url
              else sources.from_settings())
    full = citations.FULL_LIMIT if args.full is None else max(0, args.full)
    with _open(data) as conn:
        runs.get(conn, args.run_id)
        result = citations.run(conn, source, args.run_id, full_limit=full, events=False)
    cap = getattr(source, "full_cap", None)
    if cap is not None and full > cap:
        print(f"  note: without OPENALEX_API_KEY at most {cap} kept papers get full citations", file=sys.stderr)
    for message in result.errors:
        print(f"  error: {message}", file=sys.stderr)
    print(f"question-citations {jsonfmt.dumps({'run_id': args.run_id, 'looked_up': result.looked_up, 'edges': result.edges})}")


def _question_graph(args: argparse.Namespace, data: Path, raw: str) -> None:
    from collections import Counter

    from isbe.question import graph, runs

    with _open(data) as conn:
        # Before the run ends its candidates (and their keys) are still changing.
        if runs.get(conn, args.run_id)["status"] not in runs.ENDED:
            raise RuntimeError(f"question run {args.run_id} has not ended; its graph is built when it does")
        built = graph.build(conn, args.run_id)
    doc = built.doc
    kinds = Counter(n["kind"] for n in doc["nodes"])
    edges = Counter(e["kind"] for e in doc["edges"])
    print(f"graph {doc['fingerprint']} {'unchanged' if built.cached else 'built'} in {built.seconds:.1f} s: "
          f"{kinds['paper']} papers, {kinds['keyword']} keywords; "
          + ", ".join(f"{k} {n}" for k, n in sorted(edges.items())))
    print(f"communities ({len(doc['communities'])}):")
    for c in doc["communities"][: args.top]:
        print(f"  {c['id']:>3}  {c['size']:>4} papers  {', '.join(c['top_keywords']) or '-'}")
    papers = [n for n in doc["nodes"] if n["kind"] == "paper"]
    print("most central papers (PageRank):")
    for n in sorted(papers, key=lambda n: -n["metrics"]["pagerank"])[: args.top]:
        m = n["metrics"]
        print(f"  {m['pagerank']:.4f}  c{n['community'] if n['community'] is not None else '-'}  "
              f"{'bridge ' if m['bridge'] else ''}{n['label']}")
    # Older stored graphs (before VERSION 3) carry no lineage; they read stale.
    lineage = doc.get("lineage") or {"keywords": [], "edges": []}
    onset = {k["id"]: k["onset"] for k in lineage["keywords"]}
    grows = [e for e in lineage["edges"] if e["kind"] == "grows_out_of"]
    print(f"topic lineage ({len(lineage['keywords'])} keywords, {len(grows)} grow out of another):")
    for e in sorted(grows, key=lambda e: (onset[e["source"]], e["source"]))[: args.top]:
        print(f"  {onset[e['source']]}  {e['source'][3:]} ← {e['target'][3:]} ({e['weight']:.0f} citations)")
    if "coverage" in doc:
        cov = doc["coverage"]
        print(f"main paths: {len(doc['main_paths'])} citation links; full citations for "
              f"{cov['full_citations']} of {cov['papers']} papers")
    print(f"question-graph {jsonfmt.dumps({'run_id': args.run_id, **graph.summary(doc), 'cached': built.cached})}")


def _question_survey(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe.core.llm import Llm
    from isbe.question import survey

    limit = survey.MAX_PAPERS if args.max_papers is None else args.max_papers
    with _open(data) as conn:
        llm = Llm.from_env()
        stats = db.job(conn, "survey", None, lambda run, stats: survey.run(
            conn, llm, run, data, args.run_id, stats, max_papers=limit, fulltext=args.fulltext,
        ))
    print(f"question-survey ok {jsonfmt.dumps(stats)}")


class _QuestionPrinter:
    """Prints a run's stage, error and end events as they arrive."""

    def __init__(self, data: Path, run_id: str) -> None:
        import threading

        self.data, self.run_id, self.seq = data, run_id, 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        self._print()

    def _loop(self) -> None:
        while not self._stop.wait(1.0):
            self._print()

    def _print(self) -> None:
        from isbe.question import runs

        with _open(self.data) as conn:
            events = runs.events(conn, self.run_id, self.seq)
        for e in events:
            self.seq = e["seq"]
            d = e["data"]
            match e["type"]:
                case "stage":
                    print(f"  {d['stage']} {d['state']}", flush=True)
                case "state":
                    print(f"  status {d['state']}", flush=True)
                case "error":
                    print(f"  {'fatal ' if d['fatal'] else ''}error ({d['stage']}): {d['message']}", flush=True)
                case "plan":
                    queries = d["plan"]["queries"]
                    print(f"  plan v{d['plan']['version']} ({d['plan']['source']}): {len(queries)} queries", flush=True)


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


def _settings(args: argparse.Namespace, data: Path, raw: str) -> None:
    from isbe.core import settings_file

    if args.action == "export":
        if args.file is not None:
            raise ValueError("export prints the file; redirect it: isbe settings export > file.json")
        doc, invalid = settings_file.export(data, args.secrets)
        for name in invalid:
            print(f"warning: {name} in the environment is not valid; left out", file=sys.stderr)
        print(jsonfmt.dumps_pretty(doc))
        return
    if args.file is None:
        raise ValueError("name the file to import: isbe settings import FILE")
    if args.secrets:
        raise ValueError("--secrets is for export; an imported file's secrets are always saved")
    with open(args.file, "rb") as file:
        parsed = settings_file.parse(file.read(settings.MAX_FILE + 1))
    settings.save(data, parsed.changes)
    # Names only: the values may be secrets.
    for name, value in parsed.changes.items():
        locked = " (the environment overrides it)" if settings.environment(name) else ""
        print(f"{'set' if value is not None else 'cleared':<8} {name}{locked}")
    for name in parsed.unknown:
        print(f"{'skipped':<8} {name} (unknown setting)")
    set_ = sum(v is not None for v in parsed.changes.values())
    print(f"imported: {set_} set, {len(parsed.changes) - set_} cleared, {len(parsed.unknown)} skipped")


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
    "question": _question,
    "question-citations": _question_citations,
    "question-graph": _question_graph,
    "question-survey": _question_survey,
    "serve": _serve,
    "settings": _settings,
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
