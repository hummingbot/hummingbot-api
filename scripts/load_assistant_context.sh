#!/usr/bin/env bash
# Print the assistant context files for a quick startup view
set -euo pipefail
echo "---- .assistant/CONTEXT.md ----"
if [ -f .assistant/CONTEXT.md ]; then
  sed -n '1,200p' .assistant/CONTEXT.md || true
else
  echo "(missing) .assistant/CONTEXT.md"
fi
echo
echo "---- .assistant/LEXICON.md ----"
if [ -f .assistant/LEXICON.md ]; then
  sed -n '1,200p' .assistant/LEXICON.md || true
else
  echo "(missing) .assistant/LEXICON.md"
fi
echo
echo "---- .assistant/USAGE.md ----"
if [ -f .assistant/USAGE.md ]; then
  sed -n '1,200p' .assistant/USAGE.md || true
else
  echo "(missing) .assistant/USAGE.md"
fi
echo
echo "---- .assistant/SESSION_NOTES.md ----"
if [ -f .assistant/SESSION_NOTES.md ]; then
  sed -n '1,200p' .assistant/SESSION_NOTES.md || true
else
  echo "(missing) .assistant/SESSION_NOTES.md"
fi

exit 0
