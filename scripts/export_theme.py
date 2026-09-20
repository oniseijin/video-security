from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent

COLOR_TOKENS = [
    "bg",
    "surface-1",
    "surface-2",
    "line",
    "line-faint",
    "ink",
    "ink-dim",
    "ink-faint",
    "accent",
    "threat",
    "asset",
    "info",
    "success",
    "warning",
    "selection-bg",
    "selection-ink",
]

TONES = ["threat", "warning", "info", "asset"]

_VAR_RE = re.compile(r"^var\(--vs-([a-z0-9-]+)\)$")


def _load_report_theme() -> ModuleType:
    path = REPO_ROOT / "src" / "video_security" / "report_theme.py"
    spec = importlib.util.spec_from_file_location("report_theme_export", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve(tokens: dict[str, str], name: str) -> str:
    seen: set[str] = set()
    while name not in seen:
        seen.add(name)
        value = tokens[name]
        match = _VAR_RE.match(value)
        if match is None:
            return value
        name = match.group(1)
    raise SystemExit(f"cyclic token reference: {name}")


def main() -> None:
    theme = _load_report_theme()
    tokens: dict[str, dict[str, str]] = theme.TOKENS
    payload: dict[str, object] = {
        name: {
            key.replace("-", "_"): _resolve(values, key)
            for key in COLOR_TOKENS
            if key in values
        }
        for name, values in tokens.items()
    }
    payload["tones"] = {tone: f"var(--vs-{tone})" for tone in TONES}
    out_dir = REPO_ROOT / "web" / "src" / "theme"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "vs-theme.css").write_text(str(theme.SHARED_CSS), encoding="utf-8")
    (out_dir / "tokens.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    print(f"exported theme to {out_dir}")


if __name__ == "__main__":
    main()
