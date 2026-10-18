#!/usr/bin/env bash
# Install wt, the tmux config and the iTerm2 prerequisites on macOS.
# Safe to re-run: anything it would replace is backed up first.
#
#   ./install.sh              everything
#   ./install.sh --no-tmux    leave ~/.tmux.conf alone
#   ./install.sh --no-brew    do not install missing Homebrew packages
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/wt"
BIN_DIR="$HOME/.local/bin"
CONFIG_FILE="$HOME/.config/wt/config.json"
TMUX_CONF="$HOME/.tmux.conf"
STAMP="$(date +%Y%m%d-%H%M%S)"
MIN_PYTHON_MINOR=11
BREW_FORMULAS=(git fzf tmux gh)

should_install_tmux=1
should_use_brew=1
for arg in "$@"; do
  case "$arg" in
    --no-tmux) should_install_tmux=0 ;;
    --no-brew) should_use_brew=0 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) printf 'error: unknown option %s\n' "$arg" >&2; exit 1 ;;
  esac
done

note() { printf '→ %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

backup() {
  local path="$1"
  [[ -e "$path" || -L "$path" ]] || return 0
  mv "$path" "$path.bak-$STAMP"
  note "Backed up $path to $path.bak-$STAMP"
}

install_brew_packages() {
  local missing=() formula
  for formula in "${BREW_FORMULAS[@]}"; do
    command -v "$formula" >/dev/null || missing+=("$formula")
  done
  find_python >/dev/null || missing+=("python@3.12")
  if [[ ${#missing[@]} -gt 0 ]]; then
    [[ $should_use_brew -eq 1 ]] || die "missing: ${missing[*]} (re-run without --no-brew, or install them)"
    command -v brew >/dev/null || die "Homebrew is required: https://brew.sh"
    note "Installing ${missing[*]}"
    brew install "${missing[@]}"
  fi
  if [[ ! -d /Applications/iTerm.app && ! -d "$HOME/Applications/iTerm.app" ]]; then
    [[ $should_use_brew -eq 1 ]] || die "iTerm2 is not installed"
    note "Installing iTerm2"
    brew install --cask iterm2
  fi
}

# Prints the first Python interpreter that is 3.11 or newer.
find_python() {
  local candidate path
  for candidate in python3.13 python3.12 python3.11 python3; do
    path="$(command -v "$candidate" 2>/dev/null)" || continue
    if "$path" -c "import sys; sys.exit(sys.version_info < (3, $MIN_PYTHON_MINOR))" 2>/dev/null; then
      printf '%s\n' "$path"
      return 0
    fi
  done
  return 1
}

install_wt() {
  local python
  python="$(find_python)" || die "Python 3.$MIN_PYTHON_MINOR+ not found"
  if [[ -d "$INSTALL_DIR" ]] && ! diff -rq -x __pycache__ -x python.path "$REPO_DIR/wt" "$INSTALL_DIR" >/dev/null; then
    backup "$INSTALL_DIR"
  fi
  mkdir -p "$INSTALL_DIR" "$BIN_DIR"
  rsync -a --delete --exclude __pycache__ --exclude python.path "$REPO_DIR/wt/" "$INSTALL_DIR/"
  printf '%s\n' "$python" > "$INSTALL_DIR/lib/python.path"
  # A wrapper, not a symlink: wt.sh locates its lib/ directory from its own path.
  # shellcheck disable=SC2016  # $HOME must expand when the wrapper runs, not now
  printf '#!/usr/bin/env bash\nexec "$HOME/.local/share/wt/wt.sh" "$@"\n' > "$BIN_DIR/wt"
  chmod +x "$BIN_DIR/wt"
  note "Installed wt to $INSTALL_DIR (Python: $python)"
  if [[ ! -f "$CONFIG_FILE" ]]; then
    mkdir -p "$(dirname "$CONFIG_FILE")"
    printf '{\n  "linear": { "token_env": "LINEAR_API_KEY" },\n  "repos": {}\n}\n' > "$CONFIG_FILE"
    note "Created $CONFIG_FILE — add your repos (see config.example.json)"
  fi
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) note "Add to your ~/.zshrc:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
  esac
}

install_tmux_conf() {
  if [[ -f "$TMUX_CONF" ]] && cmp -s "$REPO_DIR/tmux/tmux.conf" "$TMUX_CONF"; then
    note "$TMUX_CONF already up to date"
    return
  fi
  backup "$TMUX_CONF"
  cp "$REPO_DIR/tmux/tmux.conf" "$TMUX_CONF"
  note "Installed ~/.tmux.conf"
  if tmux info >/dev/null 2>&1; then
    tmux source-file "$TMUX_CONF" && note "Reloaded the running tmux server"
  fi
}

[[ "$(uname -s)" == Darwin ]] || die "wt drives iTerm2, so it needs macOS"
install_brew_packages
install_wt
[[ $should_install_tmux -eq 0 ]] || install_tmux_conf

cat <<'EOF'

✓ Done. Remaining manual steps (see README.md → "After installing"):
  1. iTerm2 settings (iterm2/README.md) and `/terminal-setup` inside Claude Code
  2. gh auth login
  3. export LINEAR_API_KEY=... in ~/.zshrc (optional; ideas and PRs work without it)
  4. cd into a repo and run: wt doctor
EOF
