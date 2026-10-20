#!/bin/zsh
# Double-click in Finder to start optout. Installs uv if missing, then opens the UI.
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
fi
exec uv run optout
