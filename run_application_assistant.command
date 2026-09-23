#!/bin/bash
# Double-click to run the Application Assistant. Extra arguments pass through, e.g.:
#   ./run_application_assistant.command --no-record --limit 1
# Chrome must already be running with remote debugging on port 9222 (see README.md).
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "No .venv here yet — see README.md, 'Setup'."; read -r -p "Press Enter to close…"; exit 1
fi
.venv/bin/python -m assistant run "$@"
code=$?
case $code in
  0) echo "Done: every job parked for your review." ;;
  1) echo "Preflight failed: nothing was written." ;;
  2) echo "Done: at least one job needs your attention (see the report)." ;;
  3) echo "The run was stopped early (see the report)." ;;
esac
read -r -p "Press Enter to close…"
exit $code
