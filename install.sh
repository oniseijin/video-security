#!/usr/bin/env bash
#
# video-security installer.
#
# Creates a self-contained install of this repo:
#
#   <prefix>/venv/              python env with a snapshot of the current code
#   <prefix>/var/config.toml    main config (db_path -> <prefix>/var/db,
#                               artifact_dir -> the configured output volume)
#   <prefix>/var/db             the sqlite catalog (migrated from
#                               ~/.video-security on first install, original kept)
#   <bin-dir>/vs, vs-analyze, vs-import, vs-search, vs-report, vs-list
#                               wrappers that run the installed CLI with the var
#                               config (so they use <prefix>/var/db and the
#                               configured artifact dir)
#   <bin-dir>/vs-dev            wrapper that runs the WORKSPACE copy via the
#                               repo's dev venv (.venv) and ~/.video-security
#                               state — the two never share config or catalog
#
# The installed command runs the code snapshot taken at install time; re-run
# install.sh to upgrade it. Workspace/dev usage goes through vs-dev, which
# runs the editable install in the repo's .venv.
#
# The prefix defaults to ~/.local/opt/video-security; the bin dir defaults to
# ~/.local/bin. The artifact (output) dir defaults to
# /Volumes/lacie8/Ryan/video/vs and can be set with --artifact-dir.
#
# Wrappers are created for every entry point declared in pyproject.toml, so a
# future entry point is picked up automatically. All entry points accept the
# standard global --config/--db flags; the wrapper for each one passes
# --config <prefix>/var/config.toml.
#
# Usage:
#   ./install.sh                                   install (default prefix)
#   ./install.sh --artifact-dir /Volumes/x/vs      configure for another output volume
#   ./install.sh --prefix /opt/video-security      install to a specific prefix
#   ./install.sh --bin-dir /usr/local/bin          put wrappers elsewhere
#   ./install.sh --uninstall [--purge]             remove venv + wrappers (--purge
#                                                 also removes <prefix>/var, i.e. the catalog)
#
#   Re-running install.sh upgrades the code and keeps var/ (config + catalog).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PREFIX=""
BIN_DIR=""
ARTIFACT_DIR=""
UNINSTALL=0
PURGE=0
ASSUME_YES=0

usage() {
  sed -n '3,40p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix)
      [ $# -ge 2 ] || { echo "--prefix requires a value" >&2; exit 2; }
      PREFIX="$2"; shift 2 ;;
    --bin-dir)
      [ $# -ge 2 ] || { echo "--bin-dir requires a value" >&2; exit 2; }
      BIN_DIR="$2"; shift 2 ;;
    --artifact-dir)
      [ $# -ge 2 ] || { echo "--artifact-dir requires a value" >&2; exit 2; }
      ARTIFACT_DIR="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    --purge) PURGE=1; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[ -f "$SCRIPT_DIR/pyproject.toml" ] || die "pyproject.toml not found in $SCRIPT_DIR; run from the video-security repo"
[ -d "$SCRIPT_DIR/src/video_security" ] || die "src/video_security not found in $SCRIPT_DIR"

if [ -z "$PREFIX" ]; then
  PREFIX="$HOME/.local/opt/video-security"
fi
if [ -z "$BIN_DIR" ]; then
  if [ "$(id -u)" -eq 0 ]; then BIN_DIR="/usr/local/bin"; else BIN_DIR="$HOME/.local/bin"; fi
fi
if [ -z "$ARTIFACT_DIR" ]; then
  ARTIFACT_DIR="/Volumes/lacie8/Ryan/video/vs"
fi

VENV="$PREFIX/venv"
VAR="$PREFIX/var"
MARKER="video-security-installer:prefix=$PREFIX"

remove_file() {
  local f="$1"
  if [ -w "$(dirname "$f")" ]; then
    rm -f "$f"
  elif command -v sudo >/dev/null 2>&1; then
    sudo rm -f "$f"
  else
    die "cannot remove $f (no write permission and no sudo)"
  fi
}

installed_wrappers() {
  [ -d "$BIN_DIR" ] || return 0
  for f in "$BIN_DIR"/*; do
    [ -f "$f" ] || continue
    grep -Fq "$MARKER" "$f" 2>/dev/null && printf '%s\n' "$f"
  done
}

if [ "$UNINSTALL" -eq 1 ]; then
  for f in $(installed_wrappers); do
    info "removing wrapper $f"
    remove_file "$f"
  done
  if [ -d "$VENV" ]; then
    info "removing $VENV"
    rm -rf "$VENV"
  fi
  if [ -d "$VAR" ]; then
    if [ "$PURGE" -eq 1 ]; then
      info "purging $VAR (catalog deleted)"
      rm -rf "$VAR"
    else
      echo "kept $VAR (catalog and config); pass --purge to delete it too"
    fi
  fi
  rmdir "$PREFIX" 2>/dev/null || true
  rmdir "$BIN_DIR" 2>/dev/null || true
  info "uninstalled"
  exit 0
fi

info "installing video-security"
info "prefix:        $PREFIX (snapshot from $SCRIPT_DIR)"
info "bin dir:       $BIN_DIR"
info "artifact dir:  $ARTIFACT_DIR"
mkdir -p "$PREFIX"

if [ ! -x "$VENV/bin/python" ]; then
  info "creating venv"
  if command -v uv >/dev/null 2>&1; then
    uv venv "$VENV"
  else
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' \
      || die "python3 >= 3.12 required (or install uv: https://docs.astral.sh/uv/)"
    python3 -m venv "$VENV"
  fi
fi

if [ ! -x "$VENV/bin/pip" ]; then
  info "seeding pip into venv"
  "$VENV/bin/python" -m ensurepip --upgrade > /dev/null
  [ -x "$VENV/bin/pip3" ] && ln -sf pip3 "$VENV/bin/pip"
fi

info "installing package (snapshot of current code)"
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$VENV/bin/python" "$SCRIPT_DIR"
else
  "$VENV/bin/pip" install "$SCRIPT_DIR"
fi

mkdir -p "$VAR"
touch "$VAR/.metadata_never_index"
info "spotlight: $VAR excluded from indexing"

if [ ! -f "$VAR/config.toml" ]; then
  info "writing $VAR/config.toml"
  "$VENV/bin/python" - "$VAR/config.toml" "$VAR/db" "$ARTIFACT_DIR" <<'PY'
import json
import sys

config_path, db_path, artifact_dir = sys.argv[1:4]
config = (
    "[storage]\n"
    f"db_path = {json.dumps(db_path)}\n"
    f"artifact_dir = {json.dumps(artifact_dir)}\n"
    "\n"
    "[import]\n"
    "preflight_gb = 25\n"
)
with open(config_path, "w") as f:
    f.write(config)
PY
else
  info "keeping existing $VAR/config.toml"
fi

if [ ! -f "$VAR/db" ] && [ -f "$HOME/.video-security/db" ]; then
  info "copying existing catalog from ~/.video-security (original kept)"
  cp "$HOME/.video-security/db" "$VAR/db"
  for ext in "-wal" "-shm"; do
    [ -f "$HOME/.video-security/db$ext" ] \
      && cp "$HOME/.video-security/db$ext" "$VAR/db$ext" || true
  done
fi

if [ -f "$HOME/.video-security/config.toml" ] && [ ! -f "$VAR/legacy-config.toml" ]; then
  cp "$HOME/.video-security/config.toml" "$VAR/legacy-config.toml"
  echo "note: found ~/.video-security/config.toml; copied to $VAR/legacy-config.toml for reference"
fi

if [ ! -d "$ARTIFACT_DIR" ]; then
  echo "warning: artifact dir $ARTIFACT_DIR does not exist (volume not mounted?)"
  echo "         import/analyze will refuse to run until it is available"
fi

ENTRY_POINTS="$("$VENV/bin/python" - "$SCRIPT_DIR/pyproject.toml" <<'PY'
import sys
import tomllib

with open(sys.argv[1], "rb") as f:
    scripts = tomllib.load(f)["project"].get("scripts", {})
print(" ".join(scripts))
PY
)" || ENTRY_POINTS="vs"
[ -n "$ENTRY_POINTS" ] || ENTRY_POINTS="vs"

write_wrapper() {
  local name="$1" wrapper="$BIN_DIR/$1" target="$VENV/bin/$1"
  [ -x "$target" ] || die "entry point $name not found in $VENV/bin"
  if [ -e "$wrapper" ] && ! grep -Fq "$MARKER" "$wrapper" 2>/dev/null; then
    die "refusing to overwrite $wrapper (not installed by video-security); remove it or use --bin-dir"
  fi
  local tmp
  tmp="$(mktemp)"
  {
    printf '#!/bin/bash\n'
    printf '# %s\n' "$MARKER"
    printf '# installed by the video-security installer; re-run install.sh to refresh\n'
    printf 'exec %q --config %q "$@"\n' "$target" "$VAR/config.toml"
  } > "$tmp"
  chmod 0755 "$tmp"
  if [ -w "$BIN_DIR" ]; then
    mv "$tmp" "$wrapper"
  elif command -v sudo >/dev/null 2>&1; then
    sudo install -m 0755 "$tmp" "$wrapper"
    rm -f "$tmp"
  else
    rm -f "$tmp"
    die "cannot write to $BIN_DIR (no permission and no sudo); try --bin-dir"
  fi
  info "wrapper: $wrapper -> $target"
}

mkdir -p "$BIN_DIR" 2>/dev/null || true
for name in $ENTRY_POINTS; do
  write_wrapper "$name"
done

dev_tmp="$(mktemp)"
{
  printf '#!/bin/bash\n'
  printf '# %s\n' "$MARKER"
  printf '# dev wrapper: runs the WORKSPACE copy via the repo dev venv, not the install\n'
  printf 'ROOT=%q\n' "$SCRIPT_DIR"
  printf 'if [ ! -x "$ROOT/.venv/bin/vs" ]; then\n'
  printf '  echo "vs-dev: $ROOT/.venv/bin/vs is missing." >&2\n'
  printf '  echo "create it with:  cd \\"$ROOT\\" && .venv/bin/pip install -e ." >&2\n'
  printf '  exit 1\n'
  printf 'fi\n'
  printf 'exec "$ROOT/.venv/bin/vs" "$@"\n'
} > "$dev_tmp"
chmod 0755 "$dev_tmp"
if [ -w "$BIN_DIR" ]; then
  mv "$dev_tmp" "$BIN_DIR/vs-dev"
elif command -v sudo >/dev/null 2>&1; then
  sudo install -m 0755 "$dev_tmp" "$BIN_DIR/vs-dev"
  rm -f "$dev_tmp"
else
  rm -f "$dev_tmp"
  die "cannot write to $BIN_DIR (no permission and no sudo); try --bin-dir"
fi
info "wrapper: $BIN_DIR/vs-dev -> $SCRIPT_DIR/.venv (workspace code)"

if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
  chown -R "$SUDO_USER:$(id -gn "$SUDO_USER")" "$PREFIX"
  info "chowned $PREFIX to $SUDO_USER"
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo "note: $BIN_DIR is not on your PATH"
    add_line="export PATH=\"$BIN_DIR:\$PATH\""
    if [ "${ZSH_VERSION:-}" != "" ] || [ "${SHELL:-}" = */zsh ]; then
      rc="$HOME/.zshrc"
    else
      rc="$HOME/.profile"
    fi
    reply=""
    if [ "$ASSUME_YES" -eq 1 ]; then
      reply="y"
    elif [ -t 0 ]; then
      printf 'add "%s" to %s? [y/N] ' "$add_line" "$rc"
      read -r reply || reply=""
    fi
    if [ "$reply" = "y" ] || [ "$reply" = "Y" ]; then
      printf '\n# added by the video-security installer\n%s\n' "$add_line" >> "$rc"
      info "updated $rc (start a new shell or run: $add_line)"
    else
      echo "to use video-security now, run: $add_line"
    fi
    ;;
esac

info "smoke test"
"$BIN_DIR/vs" --help > /dev/null
"$BIN_DIR/vs" list > /dev/null
echo "ok: $BIN_DIR/vs runs and uses $VAR/db"

info "done"
echo
echo "  command:   $BIN_DIR/vs              (installed snapshot, $VAR/db)"
echo "  dev cmd:   $BIN_DIR/vs-dev          (workspace code, ~/.video-security state)"
echo "  config:    $VAR/config.toml"
echo "  output:    $ARTIFACT_DIR            (keyframes + reports land here)"
echo "  tonight:   $BIN_DIR/vs import /Volumes/CX-8 && $BIN_DIR/vs analyze"
echo "  upgrade:   $0    (re-run to refresh the snapshot; var/ is kept)"
echo "  uninstall: $0 --uninstall [--purge]"
