#!/usr/bin/env bash
# Installs agent-ps into ~/.local/bin.
set -euo pipefail

BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/agent-ps"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but was not found on PATH." >&2
  exit 1
fi

# Building from a clone installs what is in the tree rather than whatever
# executable happened to be committed alongside it.
if [ -d "$HERE/agent_ps" ]; then
  "$HERE/build.sh" >/dev/null
fi

mkdir -p "$BIN_DIR"

if [ -e "$BIN_DIR/agent-ps" ] && ! cmp -s "$SRC" "$BIN_DIR/agent-ps"; then
  printf 'agent-ps already exists in %s. Overwrite? [y/N] ' "$BIN_DIR"
  read -r reply
  case "$reply" in
    [yY]) ;;
    *) echo "Left the existing file in place."; exit 0 ;;
  esac
fi

install -m 755 "$SRC" "$BIN_DIR/agent-ps"
echo "Installed to $BIN_DIR/agent-ps"
echo "Remove it with: rm $BIN_DIR/agent-ps"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Note: $BIN_DIR is not on your PATH." ;;
esac
