# iTerm2 setup

wt opens one iTerm2 window per worktree, attached to that worktree's tmux session.
`install.sh` adds a **`wt` profile** for those windows (a Dynamic Profile at
`~/Library/Application Support/iTerm2/DynamicProfiles/wt.json`) and sets
`"iterm_profile": "wt"` in `~/.config/wt/config.json`. The profile inherits
everything from your Default profile, such as font, colors and size, and adds
only these keys:

| Keys | Does in Claude Code / the shell | Sends |
|---|---|---|
| ⌘ ⌫ | delete to the start of the line | `Ctrl-u` |
| ⌥ ⌫ | delete a word | `Esc DEL` |
| ⌥ ↩ | newline without submitting | `Esc CR` |
| ⌘ ← / ⌘ → | start / end of the line | a private sequence that tmux turns into `Ctrl-a` / `Ctrl-e` |
| ⌥ ← / ⌥ → | jump a word | `Esc b` / `Esc f` |
| fn ⌫ / ⌥ fn ⌫ | forward-delete a character / a word | `Ctrl-d` / `Esc d` |

Shift+Enter already inserts a newline in iTerm2 with no setup, and through tmux
thanks to `extended-keys` in the tmux config.

## Why these choices

- **Only ⌥↩ is remapped; Option itself is unchanged.** The usual advice is to
  set Option to "Esc+", but on Spanish and other ISO layouts Option types
  `| @ # [ ] { }`, and Esc+ would break that.
- **⌘← can't send `Ctrl-a`.** That is the tmux prefix, so tmux would swallow it.
  The profile sends `\e[1;9001~` instead, and the tmux config maps it back
  (`user-keys`). These two parts go together: keep both, or neither.
- **It is a separate profile.** Your Default profile and normal tabs stay as they are.
  Outside tmux, ⌘← in the `wt` profile has no meaning, which is fine because wt
  windows always run tmux.

## One manual setting

Settings → General → Selection → **Applications in terminal may access clipboard** (on).
Claude Code's `/copy` and tmux's copy-to-clipboard need it. Running
`/terminal-setup` once in Claude Code turns it on for you; restart iTerm2 afterwards.

## Notes

- The first time wt opens a window, macOS asks to let your terminal control
  iTerm2 (Automation permission). Allow it, or wt prints the `tmux attach`
  command to run yourself.
- Want the same keys in normal tabs? Settings → Profiles → Default → Keys →
  Key Mappings → Presets… → Natural Text Editing. That preset's ⌘← is `Ctrl-a`,
  so inside tmux use the `wt` profile instead.
