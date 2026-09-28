#!/bin/bash
# Double-click to start the decision model on this Mac (Kev: github.com/jaredpalmer/kev). Leave this window open
# while the Application Assistant runs; close it (or press Ctrl-C) to stop the server.
#
# It needs: git, and uv (https://docs.astral.sh/uv — "curl -LsSf https://astral.sh/uv/install.sh | sh").
# The first start downloads the checkpoint and its Qwen base model (a few GB) into ~/.cache/huggingface.
#
# Overrides, e.g.:  KEV_MODEL=jaredpalmer/kev-4b KEV_PORT=8009 ./run_kev_server.command
KEV_MODEL="${KEV_MODEL:-jaredpalmer/kev-0.8b}"   # 0.8b fits any Apple Silicon Mac; 4b and 9b want 32 GB
KEV_PORT="${KEV_PORT:-8009}"                     # must match models.local.base_url in config.toml
KEV_DIR="${KEV_DIR:-$HOME/kev}"                  # where the kev clone lives

cd "$(dirname "$0")" || exit 1
for tool in git uv; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is not installed — see the comments at the top of this file."
    read -r -p "Press Enter to close…"; exit 1; }
done
if [ -d "$KEV_DIR/.git" ]; then
  git -C "$KEV_DIR" pull --ff-only || echo "(could not update $KEV_DIR; using the clone as it is)"
else
  git clone https://github.com/jaredpalmer/kev.git "$KEV_DIR" || { echo "Could not clone kev."
    read -r -p "Press Enter to close…"; exit 1; }
fi
cd "$KEV_DIR" || exit 1
uv sync --extra serve || { echo "uv sync failed."; read -r -p "Press Enter to close…"; exit 1; }
echo "Starting $KEV_MODEL on http://127.0.0.1:$KEV_PORT — the first start downloads the weights."
uv run --extra serve python -m kev.serve --run "$KEV_MODEL" --port "$KEV_PORT"
code=$?
echo "The Kev server stopped (exit $code)."
read -r -p "Press Enter to close…"
exit $code
