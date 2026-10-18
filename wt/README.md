# wt — Linear / GitHub worktrees with a coding agent

Run `wt` inside a Git repo or any of its worktrees. The picker shows your open
Linear issues (assigned to you, active cycle) plus **New idea** and **Review a PR**.

```sh
wt ORA-38
wt --idea "a new idea"
wt --pr 42
wt --idea "experiment" --base HEAD
wt ORA-38 --agent codex            # skip the agent picker
wt ORA-38 --model opus --effort high   # skip Claude's model and effort pickers
wt ORA-38 --no-open
wt open ORA-38
wt list
wt rm ORA-38
wt rm                              # infer the current managed worktree
wt doctor
```

New worktrees live in `../<repo>-worktrees/` unless the repo's config says
otherwise. Issue and idea branches (`feature/<ID>-<slug>`, `idea/<slug>`) start
at the fetched origin default branch; the repo's `base` setting or `--base`
overrides that. Review branches (`review/pr-<n>`) start at
`refs/pull/<number>/head`, including fork PRs, and record both head and target
commit IDs.

## Agents

A second picker asks which agent to launch: `claude` (default), `opencode` or
`codex`. It is skipped when the answer is already known, in this order:
`--agent`, `$WT_AGENT`, `"agent"` in the config. With no terminal, or with
`--no-open`, it is `claude`. Each worktree remembers its agent, so `wt open`
relaunches the same one.

For `claude`, two more pickers set the model (`fable`, `opus`, `sonnet`, `haiku`)
and the reasoning effort (`low`, `medium`, `high`, `xhigh`, `max`). The first row
of each, **Claude Code default**, passes nothing and leaves Claude Code's own
setting. `--model` / `--effort`, or `"claude": { "model": …, "effort": … }` in
the config, skip the matching picker; `--model` also accepts a full model name.
The choice is saved with the worktree and reused by `wt open`.

One iTerm2 window attaches to each repo-qualified tmux session. Issue prompts
start with `/plan`; review prompts start with `/code-review`, and Codex review
sessions use the read-only sandbox.
**The first prompt is typed but never submitted: edit it and press Enter.**
An idea has nothing written down yet, so it gets no context file and no prompt:
the agent opens with an empty input.
First-run login/trust/update screens are left for you; the prompt is printed if
the agent's input box cannot be detected. `wt open` never injects another prompt
into an existing session. It starts a fresh agent when the tmux session no
longer exists.

`wt` removes inherited `NO_COLOR` for the agent and waits for a tmux client to
attach before starting it, so the agent can detect terminal colors.

Context files (`ISSUE-<ID>.md`, `PR-<number>.md`) are excluded
with repo-local `.git/info/exclude` entries. Ownership metadata is stored in
the common Git directory under `wt/`, separate from tracked files.

## Dependency bootstrap

New worktrees run the repo's configured `bootstrap` command argument list in
their root before launching the agent, including with `--no-open`. Configuration
is personal, not loaded from the checked-out branch.

- `wt --idea example --skip-setup`: deliberately skip dependency setup.
- `wt setup <id>`: retry or refresh setup; id can be omitted inside a managed worktree.
- `wt open <id>`: open without reinstalling. Failed/interrupted setup blocks launch.
- `wt open <id> --skip-setup`: explicitly bypass that block.
- PR setup asks for confirmation every time, because it runs the reviewed
  branch's code with your permissions. Noninteractive PR setup is skipped unless
  `--trust-setup` is supplied.
- Failed setup preserves the worktree and records `setup=failed` in `wt list`.
  Fix the failure, run `wt setup <id>`, then `wt open <id>`.

## Personal configuration

`~/.config/wt/config.json` holds non-secret settings. `WT_CONFIG` can select a
different file. Repo entries use the main checkout's absolute path, and every
key is optional; a repo with no entry still works.

```json
{
  "agent": "claude",
  "claude": { "model": "opus", "effort": "high" },
  "iterm_profile": "Default",
  "linear": { "token_env": "LINEAR_API_KEY" },
  "repos": {
    "/absolute/path/to/repo": {
      "base": "origin/staging",
      "worktrees_dir": "/absolute/path/to/worktrees",
      "env_files": [".env.local"],
      "bootstrap": ["pnpm", "install"]
    }
  }
}
```

`env_files` are gitignored files symlinked from the main checkout. Their
contents are shared: editing through the symlink edits the original.

Linear uses the personal API key in the named environment variable (Linear →
Settings → Security & access → Personal API keys). It is only sent to
`api.linear.app`. A repo tracked in a different Linear workspace names its own
variable with `"linear": { "token_env": "OTHER_LINEAR_API_KEY" }` inside its
`repos` entry. When that variable is not exported, it is read from the repo's
`env_files` in the main checkout. With no key, the picker still offers ideas and PRs. GitHub goes
through `gh`, which owns its own credentials (`gh auth login`).

Requirements: Git, Python >=3.11, fzf, tmux, gh, iTerm2 and at least one agent.
No additional Python packages are needed. `lib/python.path` records the
interpreter; `WT_PYTHON` overrides it.

## Cleanup

`wt rm` operates only on its registered worktrees. Tracked changes, nonignored
untracked files, and unmerged commits block ordinary removal. Squash-merge
checks require the merged PR's head SHA to equal the local branch tip, so new
commits made after a merge are not silently discarded. A review checkout with
no local commits is always removable. `wt rm <id> --force` explicitly discards
that worktree's changes and unmerged branch. Ignored caches and generated
context are removed with the worktree; env symlink targets are preserved. The
task's tmux session is killed last.

If creation fails partway through, the path and recovery command are printed.
The tool preserves that directory for inspection rather than silently deleting
it. No remote branch deletion, comments, approvals, or merges occur.

## Install and verification

`~/.local/bin/wt` is a two-line wrapper that execs `~/.local/share/wt/wt.sh`.

```sh
cd ~/.local/share/wt
bash -n wt.sh new-worktree.sh rm-worktree.sh lib/common.sh
shellcheck -x wt.sh new-worktree.sh rm-worktree.sh lib/common.sh
"$(head -1 lib/python.path)" -m unittest discover -s tests -v
```
