# shellcheck shell=bash
WT_PYTHON="${WT_PYTHON:-}"
if [[ -z "$WT_PYTHON" && -f "$SCRIPT_DIR/lib/python.path" ]]; then
  IFS= read -r WT_PYTHON < "$SCRIPT_DIR/lib/python.path"
fi
WT_PYTHON="${WT_PYTHON:-python3}"
wt_backend() {
  "$WT_PYTHON" "$SCRIPT_DIR/lib/wt.py" "$@"
}
