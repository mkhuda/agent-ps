#!/bin/sh
# Installs agent-ps into ~/.local/bin.
#
# Works two ways. Piped from the web it downloads the executable; run from a
# clone it builds one from the tree, so a contributor always installs what they
# are looking at rather than whatever was committed. Written for POSIX sh
# because `curl | sh` does not give you bash.
set -eu

REPO="${AGENT_PS_REPO:-mkhuda/agent-ps}"
REF="${AGENT_PS_REF:-main}"
URL="${AGENT_PS_URL:-https://raw.githubusercontent.com/$REPO/$REF/agent-ps}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
TARGET="$BIN_DIR/agent-ps"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but was not found on PATH." >&2
  exit 1
fi

# $0 names a real file when the script is one, and names the shell itself when
# it arrives down a pipe. `sh install.sh` gives a bare name with no directory,
# so that has to be turned into a path before it can be tested.
HERE=""
case "$0" in
  */*) SELF="$0" ;;
  *) SELF="./$0" ;;
esac
[ -f "$SELF" ] && HERE=$(CDPATH= cd -- "$(dirname -- "$SELF")" && pwd)

mkdir -p "$BIN_DIR"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT INT TERM

if [ -n "$HERE" ] && [ -f "$HERE/agent_ps/__init__.py" ]; then
  sh "$HERE/build.sh" >/dev/null
  cp "$HERE/agent-ps" "$STAGE/agent-ps"
  SOURCE="this tree"
else
  if ! curl -fsSL "$URL" -o "$STAGE/agent-ps"; then
    echo "Could not download $URL" >&2
    exit 1
  fi
  if [ -n "${AGENT_PS_URL:-}" ]; then SOURCE="$URL"; else SOURCE="$REPO@$REF"; fi
fi

# A 404 page is valid text and would install happily, so check it runs first.
chmod +x "$STAGE/agent-ps"
if ! VERSION=$("$STAGE/agent-ps" --version 2>/dev/null); then
  echo "The downloaded file is not a working agent-ps." >&2
  exit 1
fi

if [ -e "$TARGET" ] && ! cmp -s "$STAGE/agent-ps" "$TARGET"; then
  if [ -t 0 ]; then
    printf 'agent-ps already exists in %s. Overwrite? [y/N] ' "$BIN_DIR"
    read -r reply
    case "$reply" in
      [yY]) ;;
      *) echo "Left the existing file in place."; exit 0 ;;
    esac
  else
    echo "agent-ps already exists in $BIN_DIR. Re-run with BIN_DIR set, or" >&2
    echo "remove it first." >&2
    exit 1
  fi
fi

install -m 755 "$STAGE/agent-ps" "$TARGET"
echo "Installed $VERSION to $TARGET, from $SOURCE"
echo "Remove it with: rm $TARGET"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Note: $BIN_DIR is not on your PATH." ;;
esac
