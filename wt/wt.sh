#!/usr/bin/env bash
# wt — start a worktree from a Linear issue, a fresh idea, or a GitHub PR to
# review, in whichever repo you are standing in. The backend picks the agent.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

start_idea() {
  local name="${1:-}"
  if [[ -z "$name" ]]; then
    read -rp 'Idea name: ' name < /dev/tty || true
  fi
  [[ -n "$name" ]] || { printf 'Cancelled.\n'; exit 0; }
  exec "$SCRIPT_DIR/new-worktree.sh" --idea "$name" --go
}

start_review() {
  local rows selection number
  require_picker
  printf '→ Fetching open PRs…\n' >&2
  rows="$(wt_backend prs)"
  [[ -n "$rows" ]] || { printf 'No open PRs.\n'; exit 0; }
  selection="$(printf '%s\n' "$rows" | wt_fzf --delimiter=$'\t' --with-nth=2.. \
    --prompt='PR > ' --header='Pick a PR to review (Esc to cancel)')" \
    || { printf 'Cancelled.\n'; exit 0; }
  number="${selection%%$'\t'*}"
  exec "$SCRIPT_DIR/new-worktree.sh" --pr "$number" --go
}

require_picker() {
  [[ -t 0 && -t 1 ]] || { printf 'error: picker needs a terminal; use an issue ID, --idea, or --pr.\n' >&2; exit 1; }
  command -v fzf >/dev/null || { printf 'error: install the picker with brew install fzf.\n' >&2; exit 1; }
}

case "${1:-}" in
  rm) shift; exec "$SCRIPT_DIR/rm-worktree.sh" "$@" ;;
  open|setup|list|doctor) wt_backend "$@"; exit ;;
  -h|--help|help) wt_backend help; exit ;;
esac
if [[ $# -gt 0 ]]; then
  exec "$SCRIPT_DIR/new-worktree.sh" --go "$@"
fi

require_picker
wt_backend check-repo
printf '→ Fetching your open Linear issues…\n' >&2
# Keep the idea and PR paths available even if Linear authentication/network fails.
issues="$(wt_backend tickets)" || issues=""
idea_row=$'__NEW_IDEA__\t✨ New idea — worktree with no Linear issue'
review_row=$'__REVIEW_PR__\t🔍 Review a PR — check out an open PR to review'
list="$idea_row
$review_row"
[[ -z "$issues" ]] || list="$list
$issues"
selection="$(printf '%s\n' "$list" | wt_fzf --delimiter=$'\t' --with-nth=2.. \
  --prompt='worktree > ' --header='Pick a Linear issue, new idea, or PR (Esc to cancel)')" \
  || { printf 'Cancelled.\n'; exit 0; }
task_id="${selection%%$'\t'*}"
case "$task_id" in
  __NEW_IDEA__) start_idea ;;
  __REVIEW_PR__) start_review ;;
  '') exit 0 ;;
  *) exec "$SCRIPT_DIR/new-worktree.sh" "$task_id" --go ;;
esac
