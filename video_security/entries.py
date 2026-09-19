import sys

from video_security.cli import app


def analyze() -> None:
    sys.argv[0] = "vs"
    sys.argv[1:1] = ["analyze"]
    app()


def import_clips() -> None:
    sys.argv[0] = "vs"
    sys.argv[1:1] = ["import"]
    app()


def search() -> None:
    sys.argv[0] = "vs"
    sys.argv[1:1] = ["search"]
    app()


def report() -> None:
    sys.argv[0] = "vs"
    sys.argv[1:1] = ["report"]
    app()


def list_jobs() -> None:
    sys.argv[0] = "vs"
    sys.argv[1:1] = ["list"]
    app()