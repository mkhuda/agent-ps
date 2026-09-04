#!/usr/bin/env bash
# Builds the single-file executable from the package.
#
# zipapp is in the standard library, so this needs nothing installed and the
# result runs anywhere python3 does. The package is staged under a root that
# holds only an entry point, so agent_ps stays an importable package inside the
# archive rather than becoming its top level.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

cp -R "$HERE/agent_ps" "$STAGE/agent_ps"
find "$STAGE" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

cat > "$STAGE/__main__.py" <<'PY'
import sys

from agent_ps.__main__ import main

sys.exit(main())
PY

# zipapp records each file's modification time, and staging stamps them with
# now, so an unchanged tree would still produce a different archive on every
# build. Flattening them makes the artefact a function of the source alone,
# which is what lets anyone check the committed executable against the tree.
find "$STAGE" -exec touch -t 202001010000 {} +

python3 -m zipapp "$STAGE" \
  --output "$HERE/agent-ps" \
  --python "/usr/bin/env python3" \
  --compress
chmod +x "$HERE/agent-ps"
echo "Built $HERE/agent-ps ($(wc -c < "$HERE/agent-ps" | tr -d ' ') bytes)"
