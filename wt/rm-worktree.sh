#!/usr/bin/env bash
# Port of rm-worktree.sh: common-dir discovery, squash-merge checking, explicit
# force, and tmux shutdown LAST. Installed outside worktrees, so re-exec from a
# main-checkout copy is no longer needed. The backend leaves the target cwd
# before removal and checks registered ownership rather than deleting a glob.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
wt_backend rm "$@"
