# iTerm2 setup

wt opens one iTerm2 window per worktree, attached to that worktree's tmux session.
`install.sh` adds a **`wt` profile** for those windows (a Dynamic Profile at
`~/Library/Application Support/iTerm2/DynamicProfiles/wt.json`) and sets
`"iterm_profile": "wt"` in `~/.config/wt/config.json`. It inherits window size
and other settings from your Default profile, and adds a theme, a font and keys.

## Theme

- **Colors:** [Catppuccin](https://catppuccin.com) **Mocha** in macOS dark mode
  and **Latte** in light mode. The profile switches automatically ("Use separate
  colors for light and dark mode"). The colors are copied from the official
  [catppuccin/iterm](https://github.com/catppuccin/iterm) port (MIT).
- **Font:** JetBrains Mono Nerd Font Mono, 13 pt. The Mono variant keeps icons
  one cell wide, so TUIs stay aligned. `install.sh` installs it with Homebrew.
- **tmux and the wt pickers** use the terminal's palette by color *name* (blue,
  brightblack, default) rather than hex values, so they switch flavor along with iTerm2.
- **Claude Code:** run `/theme` and pick **auto**, so it follows light and dark too.
  A custom Catppuccin theme for Claude would be locked to one flavor.

## Keys

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
