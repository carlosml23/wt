#!/usr/bin/env bash
# Port of new-worktree.sh for a personal installation.
# The shared backend resolves Git from the CALLER's cwd, validates inputs and
# credentials before creation, fetches origin, writes excluded context, links
# configured ignored env files, and starts the agent with an UNSENT first prompt.
# Keeping lifecycle operations together also locks concurrent create/remove calls.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
wt_backend create "$@"
