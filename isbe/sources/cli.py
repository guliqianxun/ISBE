"""`isbe sources …`.

Operations go through `registry.start` like the console's, then this process
waits for them: a job is reported once it has left `running` and no process
holds the execution lock, so its thread has finished.
"""

import argparse
import os
import time
from pathlib import Path
from typing import Any

from isbe.core import db, jsonfmt

MAX_FILE = 64 * 1024
POLL = 0.1


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The `sources` subcommands, named and documented as clap derived them."""
    # clap's `global = true`: --data is accepted after these subcommands too.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data", default=argparse.SUPPRESS, help="Data directory holding topics/, memory/, reports/ and isbe.db.")
    sub = parser.add_subparsers(dest="sources_command", required=True, metavar="COMMAND")

    def command(name: str, help: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help, description=help, parents=[common])

    command("search-list", "List saved topic web-search plans.")
    p = command("search-save", "Save a JSON/YAML search plan. Pass the current revision to update it.")
    p.add_argument("path", type=Path, metavar="PATH")
    p.add_argument("--revision", metavar="REVISION")
    p = command("search", "Run a saved web-search plan once, without automatic retries.")
    p.add_argument("id", metavar="ID")
    p = command("recheck", "Recheck original-page dates for the next 20 materials of a saved plan.")
    p.add_argument("id", metavar="ID")
    p.add_argument("--after", metavar="AFTER")
    command("list", "Show configured sources and their most recent collection status.")
    command("export", "Export source configuration as JSON.")
    p = command("import", "Add sources from a JSON or YAML file; existing IDs are never overwritten.")
    p.add_argument("path", type=Path, metavar="PATH")
    p = command(
        "discover", "Discover and trial sources within this URL's origin. Successful new sources are enabled."
    )
    p.add_argument("url", metavar="URL")
    p.add_argument("--topic", required=True, metavar="TOPIC")
    p.add_argument(
        "--candidates-only",
        action="store_true",
        help="Keep newly discovered sources disabled, even after a successful trial.",
    )
    p = command("collect", "Collect the named sources, or all enabled sources when no IDs are supplied.")
    p.add_argument("ids", nargs="*", metavar="IDS")


def _read(path: Path, what: str) -> str:
    try:
        with open(path, "rb") as file:
            raw = file.read(MAX_FILE + 1)
    except OSError as error:
        # "No such file or directory (os error 2)", without the path Python adds.
        raise OSError(f"{error.strerror} (os error {error.errno})") from None
    if len(raw) > MAX_FILE:
        raise ValueError(f"{what} configuration must not exceed 64 KiB")
    return raw.decode("utf-8")


def run(data: Path, args: argparse.Namespace) -> None:
    from isbe import search
    from isbe.sources import registry
    from isbe.topics import decode

    conn = db.connect(data / "isbe.db")
    try:
        registry.recover(data)
        command: str = args.sources_command
        operations: list[registry.Operation]
        if command == "search-list":
            print(jsonfmt.dumps_pretty(search.list(conn)))
            return
        if command == "search-save":
            plan = decode.decode(_read(args.path, "Search"), search.SearchPlan)
            expected = plan.id if args.revision is not None else None
            print(jsonfmt.dumps(search.save(conn, plan, args.revision, expected)))
            return
        if command == "search":
            plan = search.SearchPlan.model_validate(search.get(conn, args.id)["config"])
            operations = [registry.Search(plan)]
        elif command == "recheck":
            operations = [registry.Recheck(args.id, args.after)]
        elif command == "list":
            print(jsonfmt.dumps_pretty(registry.list(conn)))
            return
        elif command == "export":
            print(jsonfmt.dumps_pretty(registry.export(conn)))
            return
        elif command == "import":
            sources = decode.decode(_read(args.path, "Source"), _import_file()).sources
            print(jsonfmt.dumps(registry.import_(conn, sources)))
            return
        elif command == "discover":
            operations = [registry.Discover(args.url, args.topic, not args.candidates_only)]
        else:
            ids: list[str] = args.ids or [
                v["config"]["id"]
                for v in registry.list(conn)
                if isinstance(v["config"], dict)
                and v["config"].get("enabled") is True
                and isinstance(v["config"].get("id"), str)
            ]
            operations = [registry.Collect(id) for id in ids]
        failures = sum(not _wait(conn, data, operation, i) for i, operation in enumerate(operations))
        if failures:
            raise RuntimeError(f"{failures} source operations failed or were incomplete")
    finally:
        conn.close()


def _wait(conn: Any, data: Path, operation: Any, index: int) -> bool:
    """Start one operation, print its finished job; true if it succeeded."""
    from isbe.sources import registry

    id = f"cli-{db.sha256(f'{os.getpid()}-{time.time_ns()}')}-{index}"
    registry.start(data, operation, id)
    while True:
        idle = registry.recover(data)
        result = registry.job(conn, id)
        if result["state"] != "running" and idle:
            print(jsonfmt.dumps_pretty(result))
            return bool(result["state"] == "succeeded")
        time.sleep(POLL)


def _import_file() -> Any:
    """`{"sources": [...]}`, as `isbe sources import` reads it (defined on use, so that
    building the parser does not load the registry)."""
    from pydantic import BaseModel, ConfigDict

    from isbe.sources.registry import Source

    class Import(BaseModel):
        model_config = ConfigDict(extra="forbid")

        sources: list[Source]

    return Import
