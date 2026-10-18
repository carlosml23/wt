#!/usr/bin/env python3
"""Shared API, Git ownership and terminal support for the wt scripts.

Python >=3.11; standard library only. Credentials never leave this process as
CLI arguments or command output. All remote operations are reads.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shlex
import shutil
import ssl
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

LINEAR_API_URL = 'https://api.linear.app/graphql'
# Assigned to the key's owner, in the team's active cycle, not Done/Cancelled.
LINEAR_TICKETS_QUERY = ('query{viewer{assignedIssues(first:50, orderBy:updatedAt, filter:{state:{type:{nin:'
                        '["completed","canceled"]}}, cycle:{isActive:{eq:true}}}){nodes{identifier title}}}}')
LINEAR_ISSUE_QUERY = ('query($key:String!,$num:Float!){issues(filter:{team:{key:{eq:$key}},number:{eq:$num}},'
                      'first:1){nodes{identifier title description url}}}')

# Coding agents a worktree can launch: command -> picker label.
AGENTS = {'claude': 'Claude Code (default)', 'opencode': 'opencode', 'codex': 'Codex'}
DEFAULT_AGENT = 'claude'
# Claude Code's --model aliases and --effort levels; "default" passes no flag.
KEEP_DEFAULT = 'default'
CLAUDE_MODELS = {KEEP_DEFAULT: 'Claude Code default — whatever it is set to', 'fable': 'Fable',
                 'opus': 'Opus', 'sonnet': 'Sonnet', 'haiku': 'Haiku'}
CLAUDE_EFFORTS = (KEEP_DEFAULT, 'low', 'medium', 'high', 'xhigh', 'max')
# What each agent's screen shows once its input box accepts text: a banner that
# proves the agent (not a shell) drew the screen, and the composer itself.
COMPOSERS = {'claude': ('claude code', r'^\s*❯'),
             'opencode': ('', r'Ask anything'),
             'codex': ('codex', r'^\s*›')}
BLOCKERS = r'trust this|do you trust|sign in|log in|update available|press enter to continue|select an account|model:\s+loading'
READY_TIMEOUT = 35
# Codex draws its composer, then replaces it with an update or trust dialog a
# moment later, so the composer must stay ready this long before anything is typed.
SETTLE_SECONDS = 2
PR_FIELDS = 'number,title,url,headRefName,baseRefName,author,body,headRefOid,state'


class Error(Exception):
    pass


def note(message):
    print(message, file=sys.stderr, flush=True)


def run(argv, *, cwd=None, check=True, input=None, timeout=120, env=None):
    try:
        result = subprocess.run([str(x) for x in argv], cwd=cwd, input=input,
                                text=True, capture_output=True, timeout=timeout,
                                env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Error(f"Could not run {Path(str(argv[0])).name}: {type(exc).__name__}") from None
    if check and result.returncode:
        raise Error((result.stderr or result.stdout).strip() or f"{argv[0]} failed")
    return result


def git(cwd, *args, check=True):
    return run(['git', '-C', cwd, *args], check=check,
               env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})


def slug(text):
    normalized = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    value = re.sub(r'[^a-z0-9]+', '-', normalized.lower()).strip('-')[:40].rstrip('-')
    if not value:
        raise Error('The name needs at least one letter or digit.')
    return value


def safe_text(text):
    return ''.join(ch for ch in str(text) if ch in '\n\t' or ord(ch) >= 32 and ord(ch) != 127)


def row_text(text):
    return re.sub(r'\s+', ' ', safe_text(text)).strip()


def load_config():
    path = Path(os.environ.get('WT_CONFIG', '~/.config/wt/config.json')).expanduser()
    try:
        config = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(config, dict):
            raise ValueError('object expected')
        return config
    except (OSError, ValueError):
        raise Error(f'Invalid configuration: {path}') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # In particular, do not forward credentials to an SSO/cross-host redirect.
        return None


def request_json(url, headers, *, data=None, proxy=None, ca_file=None):
    """GET url, or POST data as JSON when given; returns (body, response headers)."""
    if urllib.parse.urlsplit(url).scheme != 'https':
        raise Error('Authenticated API requests require HTTPS.')
    context = ssl.create_default_context()
    if ca_file:
        context.load_verify_locations(os.path.expanduser(ca_file))
    handlers = [NoRedirect(), urllib.request.HTTPSHandler(context=context)]
    if proxy is not None:
        handlers.append(urllib.request.ProxyHandler({'https': proxy} if proxy else {}))
    opener = urllib.request.build_opener(*handlers)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers = {'Content-Type': 'application/json', **headers}
    req = urllib.request.Request(url, data=body, headers={'Accept': 'application/json', **headers})
    host = urllib.parse.urlsplit(url).netloc
    # urllib's socket timeout does not bound macOS DNS resolution. A daemon
    # worker bounds the entire read (including DNS) without keeping the CLI
    # alive when the resolver is unavailable.
    outcome = queue.Queue(maxsize=1)
    def fetch():
        try:
            with opener.open(req, timeout=25) as response:
                outcome.put((True, (json.load(response), dict(response.headers))))
        except Exception as exc:
            outcome.put((False, exc))
    threading.Thread(target=fetch, daemon=True).start()
    try:
        ok, value = outcome.get(timeout=25)
        if not ok:
            raise value
        return value
    except urllib.error.HTTPError as exc:
        raise Error(f'{host}: HTTP {exc.code}. Check the API key and its access; no worktree was created by this request.') from None
    except queue.Empty:
        raise Error(f'{host}: API/DNS request timed out after 25 seconds. Check the network and retry.') from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, socket.gaierror):
            raise Error(f'{host}: DNS lookup failed. Check the network and retry.') from None
        raise Error(f'{host}: connection/TLS failure. Check network, proxy and CA configuration (TLS verification remains enabled).') from None
    except (TimeoutError, ssl.SSLError, OSError):
        raise Error(f'{host}: connection/TLS failure. Check network, proxy and CA configuration (TLS verification remains enabled).') from None
    except (ValueError, UnicodeError):
        raise Error(f'{host}: expected JSON, but received an invalid API response.') from None


def env_file_value(paths, name):
    """The value of NAME=... in the first of these dotenv files that defines it."""
    for path in paths:
        try:
            lines = Path(path).read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            key, found, value = line.strip().removeprefix('export ').partition('=')
            if found and key.strip() == name:
                return value.strip().strip('"\'')
    return None


def linear_query(config, query, variables=None):
    cfg = config.get('linear', {})
    name = cfg.get('token_env', 'LINEAR_API_KEY')
    token = os.environ.get(name) or env_file_value(cfg.get('env_files', []), name)
    if not token:
        raise Error(f'Linear credentials missing: export {name} or put it in one of the repo\'s env_files (Linear → Settings → Security & access → Personal API keys), or use --idea.')
    response = request_json(LINEAR_API_URL, {'Authorization': token},
                            data={'query': query, 'variables': variables or {}},
                            proxy=cfg.get('proxy'), ca_file=cfg.get('ca_file'))[0]
    if response.get('errors'):
        raise Error('Linear API error: ' + row_text(response['errors'][0].get('message', 'unknown')))
    return response.get('data') or {}


def linear_tickets(config):
    return linear_query(config, LINEAR_TICKETS_QUERY)['viewer']['assignedIssues']['nodes']


def linear_context(config, key):
    team, _, number = key.rpartition('-')
    nodes = linear_query(config, LINEAR_ISSUE_QUERY, {'key': team, 'num': int(number)})['issues']['nodes']
    if not nodes:
        raise Error(f'{key} was not found in Linear. Nothing was created.')
    issue = nodes[0]
    title = issue.get('title') or key
    body = (f"# {key}: {safe_text(title)}\n\n{safe_text(issue.get('url') or '')}\n\n---\n\n"
            + (safe_text(issue.get('description') or '') or '_(no description in Linear)_') + '\n')
    return title, body


class Repo:
    def __init__(self, cwd=None, config=None):
        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.config = config if config is not None else load_config()
        value = git(self.cwd, 'rev-parse', '--path-format=absolute', '--git-common-dir').stdout.strip()
        self.common = Path(value).resolve()
        records = self.worktrees()
        if not records or records[0].get('bare'):
            raise Error('wt needs a normal Git checkout (not a bare repository).')
        self.main = Path(records[0]['worktree']).resolve()
        self.settings = self.config.get('repos', {}).get(str(self.main), {})
        # A repo tracked in another Linear workspace names its own key, which
        # may live in the repo's env files instead of the shell profile.
        env_files = [str(self.main / x) for x in self.settings.get('env_files', []) if isinstance(x, str)]
        self.config = {**self.config, 'linear': {**self.config.get('linear', {}), **self.settings.get('linear', {}),
                                                 'env_files': env_files}}
        self.root = Path(self.settings.get('worktrees_dir', self.main.parent / (self.main.name + '-worktrees'))).expanduser().resolve()
        if self.root == self.main or self.root.is_relative_to(self.main) or self.root == self.main.parent or self.root == Path('/'):
            raise Error('Worktrees directory must be a separate dedicated directory outside the main checkout.')
        self.identity = hashlib.sha256(str(self.common).encode()).hexdigest()[:10]
        self.state = self.common / 'wt'

    def worktrees(self):
        raw = git(self.cwd, 'worktree', 'list', '--porcelain', '-z').stdout
        result = []
        for block in raw.split('\0\0'):
            record = {}
            for item in block.split('\0'):
                if item:
                    key, _, value = item.partition(' ')
                    record[key] = value or True
            if 'worktree' in record:
                result.append(record)
        return result

    @contextlib.contextmanager
    def lock(self):
        self.state.mkdir(mode=0o700, exist_ok=True)
        with (self.state / 'lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def records(self):
        result = []
        for file in sorted(self.state.glob('*.json')):
            try:
                record = json.loads(file.read_text())
                if record.get('repo_id') == self.identity:
                    result.append(record)
            except (ValueError, OSError):
                raise Error(f'Invalid ownership record: {file}') from None
        return result

    def save(self, record):
        path = self.state / (record['id'] + '.json')
        temporary = path.with_suffix('.tmp')
        with temporary.open('w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(record, stream, ensure_ascii=False, indent=2)
        temporary.replace(path)

    def find(self, identifier=None):
        records = self.records()
        if not identifier:
            found = [x for x in records if self.cwd == Path(x['path']) or self.cwd.is_relative_to(Path(x['path']))]
        else:
            found = [x for x in records if identifier in {x['id'], Path(x['path']).name, x['branch']}
                     or (x['mode'] == 'issue' and identifier.upper() == x['id'])]
        if len(found) != 1:
            raise Error('Specify one managed worktree ID (see wt list); none or multiple matched.')
        self.validate(found[0])
        return found[0]

    def validate(self, record):
        path = Path(record['path'])
        root = Path(record['root'])
        if (path.is_symlink() or not path.is_absolute() or path.resolve() != path or path.parent != root
                or root == self.main or path == self.main or path.is_relative_to(self.main)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', path.name)):
            raise Error('Ownership path validation failed; refusing to touch this directory.')
        actual = [x for x in self.worktrees() if Path(x['worktree']).resolve() == path]
        if len(actual) != 1 or actual[0].get('branch') != 'refs/heads/' + record['branch']:
            raise Error('Worktree registration/branch differs from its ownership record; inspect manually.')
        if actual[0].get('locked'):
            raise Error('This worktree is locked in Git; unlock it explicitly before using wt.')

    def default_branch(self):
        value = git(self.main, 'ls-remote', '--symref', 'origin', 'HEAD').stdout
        match = re.search(r'^ref: refs/heads/(.+)\tHEAD$', value, re.M)
        if not match:
            raise Error('Cannot determine origin default branch; pass --base <ref>.')
        return match.group(1)


class GitHub:
    """Pull request reads through the gh CLI, which owns the GitHub credentials."""

    def __init__(self, repo):
        if not shutil.which('gh'):
            raise Error('Pull requests need the GitHub CLI: brew install gh, then gh auth login.')
        self.repo = repo

    def call(self, *args):
        result = run(['gh', *args], cwd=self.repo.main, check=False, timeout=60)
        if result.returncode:
            raise Error('GitHub: ' + (row_text(result.stderr) or 'gh failed'))
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise Error('GitHub: expected JSON from gh, but received an invalid response.') from None

    def pr(self, number):
        return self.call('pr', 'view', number, '--json', PR_FIELDS)

    def open_prs(self):
        return self.call('pr', 'list', '--state', 'open', '--limit', '100', '--json', 'number,title,author')

    def merged_prs(self, branch):
        return self.call('pr', 'list', '--state', 'merged', '--head', branch, '--json', 'headRefOid,state')


def dependencies(open_terminal, agent=DEFAULT_AGENT):
    required = ['git'] + (['tmux', agent, 'osascript'] if open_terminal else [])
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        raise Error('Missing commands: ' + ', '.join(missing))
    if open_terminal and not Path('/Applications/iTerm.app').exists() and not (Path.home() / 'Applications/iTerm.app').exists():
        raise Error('Install iTerm2 before opening a worktree.')


def can_pick(opening):
    return bool(opening and sys.stdin.isatty() and sys.stdout.isatty() and shutil.which('fzf'))


def pick(rows, prompt, header):
    """fzf over {value: label}; returns the chosen value."""
    result = subprocess.run(['fzf', '--delimiter=\t', '--with-nth=2..', '--no-sort', f'--prompt={prompt} > ',
                             f'--header={header} (Esc to cancel)'],
                            input='\n'.join(value + '\t' + label for value, label in rows.items()) + '\n',
                            text=True, stdout=subprocess.PIPE)
    if result.returncode in (1, 130):
        raise KeyboardInterrupt
    if result.returncode:
        raise Error(f'The {prompt} picker failed; no worktree was created.')
    return result.stdout.strip().split('\t')[0]


def select_agent(config, args, opening):
    """--agent, then $WT_AGENT, then config, then the picker; claude with no terminal."""
    agent = getattr(args, 'agent', None) or os.environ.get('WT_AGENT') or config.get('agent')
    if not agent and can_pick(opening):
        agent = pick(AGENTS, 'agent', 'Which agent should start in the worktree?')
    agent = agent or DEFAULT_AGENT
    if agent not in AGENTS:
        raise Error(f'Unknown agent {agent!r}. Supported: ' + ', '.join(AGENTS))
    return agent


def select_claude_options(config, args, agent, opening):
    """Claude Code's model and effort: flag, then config, then a picker. None keeps its own default."""
    flags = getattr(args, 'model', None), getattr(args, 'effort', None)
    if agent != 'claude':
        if any(flags):
            raise Error('--model and --effort apply to claude only.')
        return None, None
    settings = config.get('claude', {})
    model = flags[0] or settings.get('model')
    effort = flags[1] or settings.get('effort')
    if not model and can_pick(opening):
        model = pick(CLAUDE_MODELS, 'model', 'Which Claude model?')
    if not effort and can_pick(opening):
        effort = pick({x: 'Claude Code default' if x == KEEP_DEFAULT else x for x in CLAUDE_EFFORTS},
                      'effort', 'How much reasoning effort?')
    model = None if model in (None, KEEP_DEFAULT) else model
    effort = None if effort in (None, KEEP_DEFAULT) else effort
    if model and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._\[\]-]*', model):
        raise Error(f'Invalid Claude model {model!r}. Use an alias (fable, opus, sonnet, haiku) or a full model name.')
    if effort and effort not in CLAUDE_EFFORTS:
        raise Error(f'Unknown effort {effort!r}. Supported: ' + ', '.join(CLAUDE_EFFORTS[1:]))
    return model, effort


def agent_label(record):
    chosen = [record[x] for x in ('model', 'effort') if record.get(x)]
    return record.get('agent', DEFAULT_AGENT) + (f" ({', '.join(chosen)})" if chosen else '')


def agent_command(record):
    agent = record.get('agent', DEFAULT_AGENT)
    # An interactive agent must not inherit an automation runner's NO_COLOR flag.
    command = ['/usr/bin/env', '-u', 'NO_COLOR', shutil.which(agent)]
    if agent == 'claude':
        for flag in ('model', 'effort'):
            if record.get(flag):
                command += ['--' + flag, record[flag]]
    if agent == 'codex':
        command += ['-C', record['path']]
        if record['mode'] == 'pr':
            command += ['--sandbox', 'read-only']
    return command


def plan_task(repo, args):
    if sum(bool(x) for x in (args.issue, args.idea, args.pr)) != 1:
        raise Error('Choose exactly one issue ID, --idea <name>, or --pr <number>.')
    if args.pr and args.base:
        raise Error('PR reviews use their target branch; --base is only for issues and ideas.')
    if args.pr:
        if not re.fullmatch(r'[1-9][0-9]*', args.pr):
            raise Error('PR number must be a positive integer.')
        obj = GitHub(repo).pr(args.pr)
        identifier = 'pr-' + args.pr
        branch = 'review/' + identifier
        title = obj['title']
        context_name = 'PR-' + args.pr + '.md'
        mode = 'pr'
        body = ''  # Written once fetched head/target commits are pinned.
        extra = {'pr': obj}
    elif args.idea:
        identifier = slug(args.idea)
        branch = 'idea/' + identifier
        title = args.idea
        # An idea has nothing written down yet: no context file, no staged prompt.
        context_name, body = None, ''
        mode, extra = 'idea', {}
    else:
        identifier = args.issue.upper()
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*-[1-9][0-9]*', identifier):
            raise Error('Expected a Linear issue ID such as ORA-38.')
        title, body = linear_context(repo.config, identifier)
        branch = 'feature/' + identifier + '-' + slug(title)
        context_name = 'ISSUE-' + identifier + '.md'
        mode, extra = 'issue', {}
    for entry in repo.records():
        if entry['id'] == identifier or entry['branch'] == branch:
            raise Error(f'Worktree already managed. Use: wt open {identifier}')
    path = repo.root / branch.rsplit('/', 1)[-1]
    if path.exists() or path.is_symlink():
        raise Error(f'Path already exists: {path}. Nothing was overwritten.')
    if git(repo.main, 'show-ref', '--verify', '--quiet', 'refs/heads/' + branch, check=False).returncode == 0:
        raise Error(f'Branch already exists: {branch}. Nothing was overwritten.')
    git(repo.main, 'check-ref-format', '--branch', branch)
    return {'version': 1, 'id': identifier, 'mode': mode, 'title': title, 'branch': branch,
            'path': str(path), 'root': str(repo.root), 'repo_id': repo.identity,
            'main': str(repo.main), 'context_name': context_name, 'body': body,
            'session': f'wt-{slug(repo.main.name)}-{repo.identity}-{identifier}',
            'phase': 'preparing', **extra}


def add_exclusion(repo, filename):
    path = repo.common / 'info' / 'exclude'
    path.parent.mkdir(exist_ok=True)
    existing = path.read_text() if path.exists() else ''
    rule = '/' + filename
    if rule not in existing.splitlines():
        with path.open('a') as stream:
            stream.write(('\n' if existing and not existing.endswith('\n') else '') + rule + '\n')


def env_paths(repo, commit):
    paths = repo.settings.get('env_files', [])
    if not isinstance(paths, list):
        raise Error('env_files must be a list of repo-relative filenames.')
    result = []
    for rel in paths:
        value = Path(rel)
        if value.is_absolute() or '..' in value.parts or '\n' in rel or rel.startswith('-'):
            raise Error('Invalid relative env-file path in wt configuration.')
        source = repo.main / value
        if not source.is_file():
            note(f'→ Skipping missing environment file: {rel}')
            continue
        if git(repo.main, 'check-ignore', '-q', '--', rel, check=False).returncode:
            raise Error(f'Configured environment file is not ignored: {rel}')
        if git(repo.main, 'cat-file', '-e', f'{commit}:{rel}', check=False).returncode == 0:
            raise Error(f'Environment file is tracked at the new base: {rel}')
        result.append(rel)
    return result


def setup_worktree(repo, record, *, skip=False, trust=False):
    repo.validate(record)
    command = repo.settings.get('bootstrap')
    if not command:
        return
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
        raise Error('bootstrap must be a nonempty command argument list in personal wt config.')
    if skip:
        record['setup_status'] = 'skipped'
        repo.save(record)
        note('→ Dependency setup skipped explicitly.')
        return
    if record['mode'] == 'pr' and not trust:
        note('PR setup executes reviewed-branch code with local permissions and linked environment files.\nCommand: ' + shlex.join(command))
        approved = sys.stdin.isatty() and input('Trust this checkout and run setup? [y/N] ').strip().lower() == 'y'
        if not approved:
            record['setup_status'] = 'skipped'
            repo.save(record)
            note(f'→ PR setup skipped. Retry after review: wt setup {record["id"]} --trust-setup')
            return
    record['setup_status'] = 'running'
    repo.save(record)
    note('→ Installing worktree dependencies: ' + shlex.join(command))
    try:
        result = subprocess.run(command, cwd=record['path'], check=False)
        if result.returncode:
            raise Error(f'Bootstrap exited with status {result.returncode}.')
    except (OSError, Error, KeyboardInterrupt) as exc:
        record['setup_status'] = 'failed'
        repo.save(record)
        raise Error(f'Setup failed; worktree preserved. Retry: wt setup {record["id"]}\n{exc}') from None
    record['setup_status'] = 'ready'
    record['setup_head'] = git(record['path'], 'rev-parse', 'HEAD').stdout.strip()
    repo.save(record)
    note('✓ Dependency setup complete.')


def pin_review(repo, record, number):
    """Fetch a PR's head and target, and write the context and prompt against those exact commits."""
    obj = record.pop('pr')
    target = obj['baseRefName']
    git(repo.main, 'check-ref-format', 'refs/heads/' + target)
    git(repo.main, 'fetch', '--quiet', 'origin', 'refs/heads/' + target)
    base_sha = git(repo.main, 'rev-parse', 'FETCH_HEAD^{commit}').stdout.strip()
    # GitHub exposes pull/<n>/head on the base repo, so this works for forks too.
    git(repo.main, 'fetch', '--quiet', 'origin', f'refs/pull/{number}/head')
    start = git(repo.main, 'rev-parse', 'FETCH_HEAD^{commit}').stdout.strip()
    if start != obj.get('headRefOid'):
        raise Error('PR head changed during setup; rerun to capture consistent context.')
    record.update(base='origin/' + target, base_sha=base_sha, initial_head=start)
    record['body'] = (f"# PR #{number}: {safe_text(obj['title'])}\n\n{safe_text(obj['url'])}\n\n"
        f"- Author: @{safe_text((obj.get('author') or {}).get('login', 'unknown'))}\n"
        f"- Source: {safe_text(obj['headRefName'])}\n- Target: {safe_text(target)}\n"
        f"- Head commit: {start}\n- Target commit: {base_sha}\n"
        f"- Review diff: `git diff {base_sha}...{start}`\n\n## Description\n\n"
        f"{safe_text(obj.get('body') or '_(no PR description)_')}\n")
    record['prompt'] = (f"/code-review Read {record['context_name']} for context, then review this PR's changes: the diff is `git diff {base_sha}...{start}`. "
        'Flag correctness, security, regression, and test-coverage concerns with file/line references, following the repo\'s AGENTS.md / CLAUDE.md. '
        'Do not edit files, push, merge, approve, or post comments — this is a pre-merge review.')
    return start


def create(repo, args):
    opening = args.go and not args.no_open
    agent = select_agent(repo.config, args, opening)
    model, effort = select_claude_options(repo.config, args, agent, opening)
    dependencies(opening, agent)
    with repo.lock():
        record = plan_task(repo, args)
        record.update(agent=agent, model=model, effort=effort)
        note('→ Fetching origin…')
        git(repo.main, 'fetch', '--quiet', 'origin')
        if record['mode'] == 'pr':
            start = pin_review(repo, record, args.pr)
        else:
            base = args.base or repo.settings.get('base') or 'origin/' + repo.default_branch()
            start = git(repo.main, 'rev-parse', '--verify', '--end-of-options', base + '^{commit}').stdout.strip()
            record.update(base=base, base_sha=start, initial_head=start)
            if record['mode'] == 'idea':
                record['prompt'] = ''
            else:
                record['prompt'] = (f"/plan Read {record['context_name']} in full, then produce an implementation plan that satisfies its acceptance criteria. Also look into best practices and industry standards for how to implement it. Do not write any code until I approve the plan.")
        filename = record['context_name']
        if filename and git(repo.main, 'cat-file', '-e', f'{start}:{filename}', check=False).returncode == 0:
            raise Error(f'Context file is already tracked at the base: {filename}')
        links = env_paths(repo, start)
        path = Path(record['path'])
        repo.root.mkdir(parents=True, exist_ok=True)
        repo.save(record)
        try:
            git(repo.main, 'worktree', 'add', '-b', record['branch'], str(path), start)
            body = record.pop('body')
            if filename:
                add_exclusion(repo, filename)
                with (path / filename).open('x') as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(body)
            for rel in links:
                dest = path / rel
                if not dest.parent.resolve().is_relative_to(path):
                    raise Error(f'Environment destination escapes the worktree: {rel}')
                if dest.exists() or dest.is_symlink():
                    raise Error(f'Environment destination already exists: {rel}')
                if git(path, 'check-ignore', '-q', '--', rel, check=False).returncode:
                    raise Error(f'Environment destination is not ignored at this base: {rel}')
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.symlink_to(repo.main / rel)
                note(f'→ Linked {rel}')
            record.update(phase='ready', env_files=links)
            repo.save(record)
        except Exception:
            note(f'Partial setup preserved at {path}. Inspect it; use wt rm {record["id"]} --force to remove a registered partial worktree.')
            raise
        note(f"✓ Worktree created: {path}\n  Branch: {record['branch']} (base: {record['base']})\n  Agent: {agent_label(record)}\n  Context: {filename or 'none (idea)'}\n  Open: wt open {record['id']}\n  Cleanup: wt rm {record['id']}")
        setup_worktree(repo, record, skip=getattr(args, 'skip_setup', False), trust=getattr(args, 'trust_setup', False))
        if opening:
            launch(repo, record)
    return record


def tmux(*args, check=True):
    return run(['tmux', *args], check=check)


def session_target(name):
    result = tmux('list-sessions', '-F', '#{session_name}\t#{session_id}', check=False)
    for line in result.stdout.splitlines():
        candidate, _, target = line.partition('\t')
        if candidate == name and re.fullmatch(r'\$[0-9]+', target):
            return target
    return None


def composer_ready(agent, screen):
    banner, composer = COMPOSERS[agent]
    lower = screen.lower()
    return (banner in lower and re.search(composer, screen, re.M) is not None
            and re.search(BLOCKERS, lower) is None)


def launch(repo, record):
    if record.get('setup_status') in {'failed', 'running'}:
        raise Error(f'Dependency setup is incomplete. Run wt setup {record["id"]}, or wt open {record["id"]} --skip-setup.')
    agent = record.get('agent', DEFAULT_AGENT)
    dependencies(True, agent)
    repo.validate(record)
    profile = repo.config.get('iterm_profile')
    session = record['session']
    target = session_target(session)
    if target:
        owner = tmux('show-options', '-qv', '-t', target, '@wt_path').stdout.strip()
        if owner != record['path']:
            raise Error('Existing tmux session has different ownership; refusing to attach or type.')
        note('→ Reattaching existing session without changing its input.')
    else:
        command = agent_command(record)
        # exec prevents a failed agent launch from falling back into a shell
        # where a staged prompt might be interpreted as a shell command.
        gate = session + '-start'
        # Agents probe terminal colors during startup. A detached session has
        # no real terminal to answer, so wait for iTerm2 (or a manual attach).
        # Keep this in the pane so a failed GUI launch can still be recovered
        # by attaching later, without leaving a shell that could run the prompt.
        attached_query = shlex.join([shutil.which('tmux'), 'display-message', '-p', '-t']) + ' "$TMUX_PANE" ' + shlex.quote('#{session_attached}')
        launch_command = (shlex.join([shutil.which('tmux'), 'wait-for', gate])
                          + '; while [ "$(' + attached_query + ')" = "0" ]; do sleep 0.1; done; exec '
                          + shlex.join(command))
        result = tmux('new-session', '-d', '-P', '-F', '#{session_id}\t#{pane_id}', '-s', session,
                      '-x', '140', '-y', '40', '-c', record['path'], launch_command)
        target, pane = result.stdout.strip().split('\t')
        tmux('set-option', '-t', target, '@wt_path', record['path'])
        tmux('set-option', '-t', target, 'remain-on-exit', 'on')
        tmux('wait-for', '-S', gate)
        record['pane'] = pane
        repo.save(record)
        # Open the window before waiting: onboarding remains visible to the user.
        open_iterm(target, profile)
        if not record['prompt']:
            note(f'→ Launched {agent} — start typing your idea.')
            return
        deadline = time.monotonic() + READY_TIMEOUT
        ready_since = None
        while time.monotonic() < deadline:
            screen = tmux('capture-pane', '-p', '-t', pane, check=False).stdout
            running = tmux('display-message', '-p', '-t', pane, '#{pane_dead} #{pane_current_command}', check=False).stdout.strip()
            if running.startswith('1 '):
                break
            if not composer_ready(agent, screen):
                ready_since = None
            elif ready_since is None:
                ready_since = time.monotonic()
            elif time.monotonic() - ready_since >= SETTLE_SECONDS:
                tmux('send-keys', '-t', pane, '-l', '--', record['prompt'])
                note(f'→ Prompt staged in {agent}, NOT submitted. Edit it if needed, then press Enter.')
                return
            time.sleep(0.5)
        note(f'→ {agent} is awaiting setup, or its composer was not detected. Nothing was typed.\nPaste this once it is ready:\n' + record['prompt'])
        return
    open_iterm(target, profile)


def open_iterm(target, profile=None):
    command = shlex.join([shutil.which('tmux'), 'attach-session', '-t', target])
    script = Path(__file__).with_name('iterm.applescript')
    result = run(['osascript', script, command, *([profile] if profile else [])], check=False, timeout=30)
    if result.returncode:
        note('iTerm2 could not be opened automatically. Attach with: ' + command)
    else:
        note('→ Opened iTerm2.')


def merged(repo, record, head):
    # An untouched review checkout holds only commits GitHub already has.
    if record['mode'] == 'pr' and head == record['initial_head']:
        return True
    base = record['base']
    # The saved base commit is safe even when disconnected; refreshed origin
    # refs can additionally prove that commits have now landed upstream.
    bases = [record['base_sha']]
    default = git(repo.main, 'symbolic-ref', '-q', 'refs/remotes/origin/HEAD', check=False).stdout.strip()
    if base.startswith('origin/'):
        bases.append('refs/remotes/' + base)
    if default:
        bases.append(default)
    for ref in bases:
        if git(repo.main, 'merge-base', '--is-ancestor', head, ref, check=False).returncode == 0:
            return True
    # Squash merges are never ancestors, so ask GitHub; the merged PR must end
    # at this exact local commit, or later local work would be discarded.
    try:
        api = GitHub(repo)
        candidates = [api.pr(record['id'][3:])] if record['mode'] == 'pr' else api.merged_prs(record['branch'])
        return any(obj.get('state') == 'MERGED' and obj.get('headRefOid') == head for obj in candidates)
    except Error as exc:
        note(f'→ Merge status unavailable: {exc}')
    return False


def remove(repo, identifier=None, force=False):
    with repo.lock():
        record = repo.find(identifier)
        path = Path(record['path'])
        head = git(path, 'rev-parse', 'HEAD').stdout.strip()
        if not force:
            status = git(path, 'status', '--porcelain', '--untracked-files=all').stdout
            if status:
                raise Error('Worktree has tracked changes or nonignored untracked files. Commit/stash them, or explicitly use --force to discard them.')
            if not merged(repo, record, head):
                raise Error('Branch has unmerged commits (or merge status could not be verified). Keep it, or explicitly use --force.')
        # Check that the session belongs to this task BEFORE any destructive work.
        session = record['session']
        target = session_target(session) if shutil.which('tmux') else None
        if target and tmux('show-options', '-qv', '-t', target, '@wt_path').stdout.strip() != str(path):
            raise Error('Session ownership mismatch; nothing removed.')
        os.chdir(repo.main)
        # Git removes only the exact registered worktree. We do not run an
        # unconditional rm -rf if Git refuses or a locked/foreign path remains.
        git(repo.main, 'worktree', 'remove', '--force', str(path))
        git(repo.main, 'branch', '-D', '--', record['branch'])
        git(repo.main, 'worktree', 'prune')
        (repo.state / (record['id'] + '.json')).unlink()
        note(f"✓ Removed {path} and local branch {record['branch']}. Shared environment files were preserved.")
        if target:
            note('→ Closing its tmux session.')
            tmux('kill-session', '-t', target)


HELP = '''wt — Linear / GitHub worktrees with a coding agent in iTerm2 + tmux

  wt                              Pick an open Linear issue, a new idea, or a PR
  wt ORA-38                       Create a worktree for a Linear issue
  wt --idea "name"                Create an idea worktree
  wt --pr 42                      Create a PR review worktree
  wt --idea "name" --base HEAD    Override the issue/idea base
  wt ORA-38 --no-open             Prepare without tmux or iTerm2
  wt ORA-38 --agent codex         Skip the agent picker (claude | opencode | codex)
  wt ORA-38 --model opus --effort high   Skip Claude's model and effort pickers
  wt open <id>                    Open an existing worktree/session
  wt setup [id] [--trust-setup]   Install/refresh dependencies; infer id in a worktree
  wt --idea name --skip-setup     Create without installing dependencies
  wt list                         List managed worktrees in this repo
  wt rm [id] [--force]            Remove one; --force discards local work
  wt doctor                       Check local prerequisites and API access

Prompts are staged, never submitted. Press Enter inside the agent to start.
Issues use /plan and wait for your approval before implementation; PRs use
/code-review. Ideas open the agent with an empty input, ready for you to type.
Interactive creation shows an agent picker. --agent, $WT_AGENT or "agent" in the
config skip it; with no terminal it is claude. For claude, two more pickers set
the model and the reasoning effort; --model / --effort or "claude" in the config
skip them, and "default" leaves Claude Code's own setting. Saved per worktree.
Config: ~/.config/wt/config.json (override path with WT_CONFIG).
'''


def doctor(repo):
    errors = []
    for name in ('git', 'tmux', 'fzf', 'gh', 'osascript'):
        path = shutil.which(name)
        print(f"{name}: {path or 'MISSING'}")
        if not path:
            errors.append(name)
    for name in AGENTS:
        print(f"{name}: {shutil.which(name) or 'not installed'}")
    print(f'Repo: {repo.main}\nWorktrees: {repo.root}')
    for name, call in [('Git', lambda: git(repo.main, 'ls-remote', 'origin', 'HEAD')),
                       ('Linear', lambda: linear_query(repo.config, 'query{viewer{id}}')),
                       ('GitHub', lambda: GitHub(repo).call('repo', 'view', '--json', 'nameWithOwner'))]:
        try:
            call()
            print(name + ': OK')
        except Error as exc:
            print(name + ': ' + str(exc))
            errors.append(name)
    if errors:
        raise Error('Checks failed: ' + ', '.join(errors))


def cli(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in {'help', '--help', '-h'}:
        print(HELP)
        return
    action, *rest = argv
    repo = Repo()
    if action == 'check-repo':
        return
    if action == 'tickets':
        for obj in linear_tickets(repo.config):
            key = row_text(obj['identifier'])
            print(key + '\t' + key + ' — ' + row_text(obj['title']))
    elif action == 'prs':
        for obj in GitHub(repo).open_prs():
            print(f"{obj['number']}\t#{obj['number']} {row_text(obj['title'])} — @{row_text((obj.get('author') or {}).get('login', 'unknown'))}")
    elif action == 'create':
        parser = argparse.ArgumentParser(prog='wt', description=HELP)
        parser.add_argument('issue', nargs='?')
        parser.add_argument('--idea')
        parser.add_argument('--pr')
        parser.add_argument('--base')
        parser.add_argument('--go', action='store_true')
        parser.add_argument('--no-open', action='store_true')
        parser.add_argument('--skip-setup', action='store_true')
        parser.add_argument('--trust-setup', action='store_true', help='Explicitly authorize PR checkout setup code')
        parser.add_argument('--agent', choices=list(AGENTS), help='Coding agent to launch; skips the picker')
        parser.add_argument('--model', help='Claude model alias or full name; skips the model picker')
        parser.add_argument('--effort', choices=CLAUDE_EFFORTS[1:], help='Claude reasoning effort; skips the effort picker')
        create(repo, parser.parse_args(rest))
    elif action == 'open':
        parser = argparse.ArgumentParser(prog='wt open')
        parser.add_argument('id')
        parser.add_argument('--skip-setup', action='store_true')
        args = parser.parse_args(rest)
        with repo.lock():
            record = repo.find(args.id)
            if args.skip_setup:
                setup_worktree(repo, record, skip=True)
            launch(repo, record)
    elif action == 'setup':
        parser = argparse.ArgumentParser(prog='wt setup')
        parser.add_argument('id', nargs='?')
        parser.add_argument('--trust-setup', action='store_true')
        args = parser.parse_args(rest)
        with repo.lock():
            setup_worktree(repo, repo.find(args.id), trust=args.trust_setup)
    elif action == 'list':
        if rest:
            raise Error('Usage: wt list')
        for obj in repo.records():
            running = bool(shutil.which('tmux') and session_target(obj['session']))
            print(f"{obj['id']}\t{'running' if running else obj['phase']}\tsetup={obj.get('setup_status', 'not-run')}\t{agent_label(obj)}\t{obj['branch']}\t{obj['path']}")
    elif action == 'rm':
        parser = argparse.ArgumentParser(prog='wt rm')
        parser.add_argument('id', nargs='?')
        parser.add_argument('--force', action='store_true')
        args = parser.parse_args(rest)
        remove(repo, args.id, args.force)
    elif action == 'doctor':
        doctor(repo)
    else:
        raise Error('Unknown command. Run wt --help.')


if __name__ == '__main__':
    try:
        cli()
    except (Error, OSError, KeyError, ValueError) as exc:
        note('error: ' + str(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        note('Cancelled.')
        sys.exit(130)
