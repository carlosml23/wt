# wt: one worktree, one tmux session, one coding agent per task

`wt` turns a Linear issue, a new idea, or a GitHub PR into its own Git worktree.
It installs the worktree's dependencies, opens a tmux session in a new iTerm2
window, and starts Claude Code (or Codex / opencode) with a first prompt typed
and ready. You can run many tasks in parallel, and each stays isolated.

```sh
wt                 # picker: your Linear issues in the current cycle, a new idea, or an open PR
wt ORA-38          # worktree for a Linear issue; the agent starts with /plan
wt --pr 42         # review checkout; the agent starts with /code-review
wt --idea "spike"  # empty worktree for an idea
wt list            # what's open in this repo
wt rm ORA-38       # remove the worktree, local branch and tmux session (refuses if work is unmerged)
```

The full command reference is in [`wt/README.md`](wt/README.md).

This repo holds three things:

| Path | What |
|---|---|
| `wt/` | the tool (Bash + standard-library Python, tested) |
| `tmux/tmux.conf` | tmux tuned for agents: `Ctrl-a` prefix, truecolor, Shift+Enter, mouse, 100k scrollback, clipboard |
| `iterm2/README.md` | the few iTerm2 settings the setup relies on |

## Install

Requires macOS, [Homebrew](https://brew.sh), and at least one agent (`claude`, `codex` or `opencode`).

```sh
git clone https://github.com/carlosml23/wt.git ~/Documents/wt
cd ~/Documents/wt && ./install.sh
```

`install.sh` installs any missing `git fzf tmux gh python@3.12 iterm2` through
Homebrew, copies `wt/` to `~/.local/share/wt`, writes `~/.local/bin/wt`, and
installs `~/.tmux.conf`. It backs up any existing file first, as
`*.bak-<timestamp>`, and is safe to re-run. Pass `--no-tmux` to keep your own
tmux config.

To update later, run `git pull && ./install.sh`.

## After installing

1. **iTerm2:** follow [`iterm2/README.md`](iterm2/README.md) (about two minutes).
2. **GitHub:** run `gh auth login`.
3. **Linear (optional):** create a personal API key (Linear → Settings → Security & access →
   Personal API keys) and add `export LINEAR_API_KEY=lin_api_...` to `~/.zshrc`.
   Without a key, ideas and PRs still work.
4. **Per-repo settings:** edit `~/.config/wt/config.json` to set the base branch,
   the env files to symlink into new worktrees, and the dependency install command.
   See [`config.example.json`](config.example.json).
5. **Check:** `cd` into a repo and run `wt doctor`.

## Setting this up with Claude Code

Point Claude Code at this repo, e.g.
*"Set up https://github.com/carlosml23/wt on my machine following its README."*

Instructions for the agent:

1. Clone the repo to `~/Documents/wt` (or `git pull` if it is already there) and run `./install.sh`.
   If `~/.tmux.conf` already exists, show the user the diff against `tmux/tmux.conf`
   and ask before replacing it; use `--no-tmux` to keep theirs.
2. Make sure `~/.local/bin` is on `PATH` in `~/.zshrc`.
3. Ask the user which repos they will use `wt` in. For each one, read the repo to
   find its base branch (e.g. `origin/staging` if PRs target staging), its
   gitignored env files (`.env`, `.env.local`, …) and its install command
   (`pnpm install`, `uv sync`, …). Write those into `~/.config/wt/config.json`
   following `config.example.json`, keyed by the repo's absolute path.
4. Check `gh auth status`. If they are not logged in, ask the user to run `! gh auth login`.
   Ask whether they use Linear; if so, have them add `LINEAR_API_KEY` to `~/.zshrc` themselves.
   Never ask them to paste the key into the chat.
5. Walk the user through the iTerm2 steps in `iterm2/README.md`. They are GUI settings,
   so the user clicks them.
6. Verify: run `"$(cat ~/.local/share/wt/lib/python.path)" -m unittest discover -s ~/.local/share/wt/tests`,
   then `wt doctor` inside one configured repo.

## tmux cheat sheet

The prefix is **`Ctrl-a`**: press it, release, then press the key.

| Keys | Action |
|---|---|
| `Ctrl-a s` | switch between sessions (one per task) |
| `Ctrl-a v` / `Ctrl-a -` | split side by side / stacked, in the same worktree |
| `Ctrl-a h j k l` | move between panes |
| `Ctrl-a c` | new window in the same worktree |
| `Ctrl-a Enter` | scroll back (`v` to select, `y` to copy, `q` to leave) |
| `Ctrl-a d` | detach; the agent keeps running, `wt open <id>` brings it back |
| `Ctrl-a r` | reload `~/.tmux.conf` |
