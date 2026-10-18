# shellcheck shell=bash
WT_PYTHON="${WT_PYTHON:-}"
if [[ -z "$WT_PYTHON" && -f "$SCRIPT_DIR/lib/python.path" ]]; then
  IFS= read -r WT_PYTHON < "$SCRIPT_DIR/lib/python.path"
fi
WT_PYTHON="${WT_PYTHON:-python3}"
# Picker colors by terminal palette name (-1 = the terminal's own text/background),
# so fzf follows the iTerm2 profile's Catppuccin Mocha / Latte like tmux does.
export WT_FZF_COLORS='--color=16,fg:-1,bg:-1,hl:blue,fg+:-1:bold,bg+:-1,hl+:blue:bold,prompt:blue,pointer:blue,marker:green,spinner:blue,info:-1,header:-1,gutter:-1'
wt_backend() {
  "$WT_PYTHON" "$SCRIPT_DIR/lib/wt.py" "$@"
}
# Applies only to wt's own pickers; the user's FZF_DEFAULT_OPTS come last and win.
wt_fzf() {
  FZF_DEFAULT_OPTS="$WT_FZF_COLORS ${FZF_DEFAULT_OPTS:-}" fzf "$@"
}
