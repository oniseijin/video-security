import sys

from video_security.cli import app

GLOBAL_FLAGS = ("--config", "--db")


def _run(subcommand: str) -> None:
    argv = sys.argv[1:]
    pre: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in GLOBAL_FLAGS and i + 1 < len(argv):
            pre.extend([arg, argv[i + 1]])
            i += 2
        else:
            rest.append(arg)
            i += 1
    sys.argv[0] = "vs"
    sys.argv[1:] = [*pre, subcommand, *rest]
    app()


def vs_main() -> None:
    app()


def analyze() -> None:
    _run("analyze")


def import_clips() -> None:
    _run("import")


def search() -> None:
    _run("search")


def report() -> None:
    _run("report")


def serve() -> None:
    _run("serve")


def list_jobs() -> None:
    _run("list")
