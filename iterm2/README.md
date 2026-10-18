# iTerm2 setup

wt opens one iTerm2 window per worktree and attaches it to that worktree's tmux
session. iTerm2 stays close to stock; these are the settings that matter.

## 1. Natural text editing keys (Default profile)

Settings → Profiles → Default → Keys → Key Mappings → **Presets… → Natural Text Editing**.

| Keys | Does |
|---|---|
| ⌥ ← / ⌥ → | jump a word |
| ⌘ ← / ⌘ → | start / end of line |
| ⌥ ⌫ | delete a word |
| ⌘ ⌫ | delete the whole line |
| ⌥ fn ⌫ / fn ⌫ | forward delete a word / a character |

## 2. Let terminal apps use the clipboard

Settings → General → Selection → **Applications in terminal may access clipboard** (on).
Combined with the tmux config, text you select in a pane lands in the macOS clipboard.

## 3. Shift+Enter for newlines in Claude Code

Inside Claude Code, run `/terminal-setup` once. The tmux config already turns on
`extended-keys`, so Shift+Enter still reaches Claude Code through tmux.

## 4. Optional: a dedicated profile

wt uses the Default profile. To use another one, set `"iterm_profile": "<name>"`
in `~/.config/wt/config.json`.

## Notes

- The first time wt opens a window, macOS asks to let your terminal control
  iTerm2 (Automation permission). Allow it, or wt prints the `tmux attach`
  command to run yourself.
- Font, colors and window size are personal; the setup doesn't depend on them.
