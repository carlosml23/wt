import argparse
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

spec = importlib.util.spec_from_file_location('wt', Path(__file__).parents[1] / 'lib/wt.py')
wt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wt)


def args(**changes):
    values = dict(issue=None, idea='example task', pr=None, base=None, go=False, no_open=True)
    values.update(changes)
    return argparse.Namespace(**values)


class Worktrees(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wt-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.origin = self.root / 'origin.git'
        self.main = self.root / 'repo with spaces'
        wt.run(['git', 'init', '--bare', '--initial-branch=main', self.origin])
        wt.run(['git', 'init', '--initial-branch=main', self.main])
        wt.git(self.main, 'config', 'user.name', 'WT Test')
        wt.git(self.main, 'config', 'user.email', 'wt-test@example.invalid')
        wt.git(self.main, 'config', 'commit.gpgsign', 'false')
        (self.main / '.gitignore').write_text('.env\ncache/\n')
        (self.main / 'README').write_text('baseline\n')
        wt.git(self.main, 'add', '.')
        wt.git(self.main, 'commit', '-m', 'baseline')
        wt.git(self.main, 'remote', 'add', 'origin', str(self.origin))
        wt.git(self.main, 'push', '-u', 'origin', 'main')
        (self.main / '.env').write_text('TEST_VALUE=local\n')
        self.config = {'repos': {str(self.main): {'env_files': ['.env']}}}
        self.repo = wt.Repo(self.main, self.config)
        self.stderr = contextlib.redirect_stderr(io.StringIO())
        self.stderr.__enter__()
        self.addCleanup(self.stderr.__exit__, None, None, None)
        self.original_cwd = Path.cwd()
        self.addCleanup(os.chdir, self.original_cwd)
        # Hermetic by default: no personal agent preference, no real gh calls.
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('WT_AGENT', None)
        github = patch.object(wt, 'GitHub', side_effect=wt.Error('GitHub unavailable in tests'))
        github.start()
        self.addCleanup(github.stop)

    def create(self, **changes):
        return wt.create(self.repo, args(**changes))

    def create_issue(self):
        with patch.object(wt, 'linear_context', return_value=('Example issue', '# Context\n')):
            return self.create(idea=None, issue='TEST-7')

    def commit_work(self, record, name='new-code.py'):
        path = Path(record['path'])
        (path / name).write_text('print("new work")\n')
        wt.git(path, 'add', name)
        wt.git(path, 'commit', '-m', 'new work')
        return wt.git(path, 'rev-parse', 'HEAD').stdout.strip()

    def pull_request(self, head, **changes):
        obj = {'number': 42, 'title': 'Review me', 'headRefOid': head, 'headRefName': 'contributor',
               'baseRefName': 'main', 'url': 'https://github.com/a/b/pull/42',
               'author': {'login': 'someone'}, 'body': 'Full description', 'state': 'OPEN'}
        obj.update(changes)
        return obj

    def create_review(self, **changes):
        head = wt.git(self.main, 'rev-parse', 'HEAD').stdout.strip()
        wt.git(self.origin, 'update-ref', 'refs/pull/42/head', head)
        api = MagicMock()
        api.pr.return_value = self.pull_request(head, **changes)
        with patch.object(wt, 'GitHub', return_value=api):
            return self.create(idea=None, pr='42'), head

    def test_round_trip_preserves_env_and_main(self):
        record = self.create()
        path = Path(record['path'])
        self.assertEqual(record['branch'], 'idea/example-task')
        self.assertEqual(record['base'], 'origin/main')
        self.assertEqual(record['agent'], 'claude')
        self.assertEqual((path / '.env').resolve(), self.main / '.env')
        self.assertIsNone(record['context_name'])
        self.assertEqual(record['prompt'], '')
        self.assertEqual([x.name for x in path.iterdir() if x.name.startswith('IDEA')], [])
        self.assertEqual(wt.git(path, 'status', '--porcelain').stdout, '')
        (path / 'cache').mkdir()
        (path / 'cache' / 'artifact').write_text('temporary artifact')
        wt.remove(self.repo, record['id'])
        self.assertFalse(path.exists())
        self.assertTrue((self.main / '.env').exists())
        self.assertEqual(len(self.repo.worktrees()), 1)
        self.assertEqual(self.repo.records(), [])

    def test_called_from_linked_worktree_subdirectory(self):
        record = self.create()
        sub = Path(record['path']) / 'nested'
        sub.mkdir()
        nested = wt.Repo(sub, self.config)
        self.assertEqual(nested.main, self.main)
        self.assertEqual(nested.find()['id'], record['id'])
        wt.remove(nested)
        self.assertFalse(Path(record['path']).exists())

    def test_repo_can_name_its_own_linear_key(self):
        config = {'linear': {'token_env': 'LINEAR_API_KEY', 'proxy': ''},
                  'repos': {str(self.main): {'linear': {'token_env': 'OTHER_LINEAR_KEY'}}}}
        os.environ.update(LINEAR_API_KEY='default-workspace', OTHER_LINEAR_KEY='other-workspace')
        with patch.object(wt, 'request_json', return_value=({'data': {}}, {})) as request:
            wt.linear_query(wt.Repo(self.main, config).config, 'query{viewer{id}}')
            self.assertEqual(request.call_args.args[1], {'Authorization': 'other-workspace'})
            self.assertEqual(request.call_args.kwargs['proxy'], '')
            wt.linear_query(wt.Repo(self.main, {}).config, 'query{viewer{id}}')
            self.assertEqual(request.call_args.args[1], {'Authorization': 'default-workspace'})
        self.assertEqual(config['linear'], {'token_env': 'LINEAR_API_KEY', 'proxy': ''})

    def test_linear_key_falls_back_to_the_repo_env_file(self):
        (self.main / '.env').write_text('# keys\nOTHER=x\nexport REPO_LINEAR_KEY="from-file"\n')
        config = {'repos': {str(self.main): {'env_files': ['.env'], 'linear': {'token_env': 'REPO_LINEAR_KEY'}}}}
        os.environ.pop('REPO_LINEAR_KEY', None)
        with patch.object(wt, 'request_json', return_value=({'data': {}}, {})) as request:
            wt.linear_query(wt.Repo(self.main, config).config, 'query{viewer{id}}')
            self.assertEqual(request.call_args.args[1], {'Authorization': 'from-file'})
            os.environ['REPO_LINEAR_KEY'] = 'from-environment'
            wt.linear_query(wt.Repo(self.main, config).config, 'query{viewer{id}}')
            self.assertEqual(request.call_args.args[1], {'Authorization': 'from-environment'})
        os.environ.pop('REPO_LINEAR_KEY')
        (self.main / '.env').write_text('OTHER=x\n')
        with self.assertRaisesRegex(wt.Error, 'REPO_LINEAR_KEY'):
            wt.linear_query(wt.Repo(self.main, config).config, 'query{viewer{id}}')

    def test_configured_base_overrides_origin_default(self):
        wt.git(self.main, 'push', 'origin', 'main:staging')
        wt.git(self.main, 'fetch', 'origin')
        self.repo.settings['base'] = 'origin/staging'
        self.assertEqual(self.create()['base'], 'origin/staging')

    def test_duplicates_and_existing_directories_untouched(self):
        record = self.create()
        with self.assertRaisesRegex(wt.Error, 'wt open'):
            self.create()
        self.assertTrue(Path(record['path']).exists())
        target = self.repo.root / 'existing'
        target.mkdir()
        (target / 'keep').write_text('keep me')
        with self.assertRaisesRegex(wt.Error, 'already exists'):
            self.create(idea='existing')
        self.assertTrue((target / 'keep').is_file())

    def test_protect_tracked_and_untracked_and_explicit_force(self):
        record = self.create()
        path = Path(record['path'])
        (path / 'new.py').write_text('valuable new source')
        with self.assertRaisesRegex(wt.Error, 'untracked'):
            wt.remove(self.repo, record['id'])
        (path / 'new.py').unlink()
        (path / 'README').write_text('valuable tracked edit')
        with self.assertRaisesRegex(wt.Error, 'tracked changes'):
            wt.remove(self.repo, record['id'])
        wt.remove(self.repo, record['id'], force=True)
        self.assertFalse(path.exists())

    def test_unmerged_commits_block_before_removing_directory(self):
        record = self.create()
        self.commit_work(record)
        with self.assertRaisesRegex(wt.Error, 'unmerged'):
            wt.remove(self.repo, record['id'])
        self.assertTrue(Path(record['path']).exists())

    def test_squash_merge_requires_exact_local_head(self):
        record = self.create()
        head = self.commit_work(record)
        api = MagicMock()
        api.merged_prs.return_value = [{'state': 'MERGED', 'headRefOid': head}]
        with patch.object(wt, 'GitHub', return_value=api):
            self.assertTrue(wt.merged(self.repo, record, head))
            api.merged_prs.assert_called_with(record['branch'])
            newer = self.commit_work(record, 'additional.py')
            self.assertFalse(wt.merged(self.repo, record, newer))
            with self.assertRaisesRegex(wt.Error, 'unmerged'):
                wt.remove(self.repo, record['id'])

    def test_protect_foreign_and_changed_branches(self):
        record = self.create()
        wt.git(record['path'], 'switch', '-c', 'unrelated-branch')
        with self.assertRaisesRegex(wt.Error, 'registration/branch'):
            wt.remove(self.repo, record['id'], force=True)
        self.assertTrue(Path(record['path']).exists())

    def test_locked_worktree_protected(self):
        record = self.create()
        wt.git(self.main, 'worktree', 'lock', record['path'])
        with self.assertRaisesRegex(wt.Error, 'locked'):
            wt.remove(self.repo, record['id'], force=True)

    def test_tracked_environment_at_base_rejected(self):
        wt.git(self.main, 'add', '-f', '.env')
        wt.git(self.main, 'commit', '-m', 'tracked environment in this fixture only')
        with self.assertRaisesRegex(wt.Error, 'not ignored|tracked at'):
            self.create(base='HEAD')
        self.assertEqual(len(self.repo.worktrees()), 1)

    def test_issue_context_and_namespaced_session(self):
        with patch.object(wt, 'linear_context', return_value=('Fix "quoted" title', '# Text\n\\n stays literal\n')):
            record = self.create(idea=None, issue='test-12')
        self.assertEqual(record['id'], 'TEST-12')
        self.assertEqual(record['branch'], 'feature/TEST-12-fix-quoted-title')
        self.assertEqual(record['context_name'], 'ISSUE-TEST-12.md')
        self.assertIn('\\n stays literal', (Path(record['path']) / record['context_name']).read_text())
        self.assertTrue(record['session'].startswith('wt-repo-with-spaces-' + self.repo.identity))
        self.assertTrue(record['prompt'].startswith('/plan Read ISSUE-TEST-12.md'))
        self.assertIn('best practices and industry standards', record['prompt'])

    def test_same_ticket_and_repo_name_do_not_collide_across_checkouts(self):
        second = self.root / 'elsewhere' / self.main.name
        second.parent.mkdir()
        wt.run(['git', 'clone', self.origin, second])
        other = wt.Repo(second, {})
        with patch.object(wt, 'linear_context', return_value=('Shared ticket', 'Context')):
            first = self.create(idea=None, issue='TEST-12')
            another = wt.create(other, args(idea=None, issue='TEST-12'))
        self.assertNotEqual(first['session'], another['session'])
        wt.remove(self.repo, first['id'])
        self.assertTrue(Path(another['path']).exists())

    def test_pr_fetches_review_ref_and_pins_diff(self):
        record, head = self.create_review()
        self.assertEqual(record['branch'], 'review/pr-42')
        self.assertEqual(record['base'], 'origin/main')
        self.assertEqual(record['initial_head'], head)
        self.assertTrue(record['prompt'].startswith('/code-review Read PR-42.md'))
        self.assertIn(f'git diff {head}...{head}', record['prompt'])
        context = (Path(record['path']) / 'PR-42.md').read_text()
        self.assertIn('@someone', context)
        self.assertIn('Full description', context)
        self.assertEqual(self.repo.find('pr-42')['id'], 'pr-42')

    def test_pr_head_moving_during_setup_is_rejected(self):
        with self.assertRaisesRegex(wt.Error, 'head changed'):
            self.create_review(headRefOid='0' * 40)
        self.assertEqual(len(self.repo.worktrees()), 1)

    def test_untouched_review_checkout_is_removable_but_local_commits_are_not(self):
        record, _ = self.create_review()
        self.commit_work(record)
        with self.assertRaisesRegex(wt.Error, 'unmerged'):
            wt.remove(self.repo, record['id'])
        wt.git(record['path'], 'reset', '--hard', record['initial_head'])
        wt.remove(self.repo, record['id'])
        self.assertFalse(Path(record['path']).exists())

    def fake_tmux(self, calls, screens):
        # The last screen repeats once the earlier ones have each been shown once.
        remaining = list(screens)
        def fake(*values, **kw):
            calls.append(values)
            output = ''
            if values[0] == 'new-session':
                output = '$5\t%7\n'
            elif values[0] == 'capture-pane':
                output = remaining.pop(0) if len(remaining) > 1 else remaining[0]
            elif values[0] == 'display-message':
                output = '0 node\n'
            return subprocess.CompletedProcess(values, 0, output, '')
        return fake

    def launch(self, record, *screens):
        calls = []
        with patch.object(wt, 'dependencies'), patch.object(wt, 'open_iterm'), \
                patch.object(wt, 'tmux', side_effect=self.fake_tmux(calls, screens)), \
                patch.object(wt.time, 'sleep'), patch.object(wt, 'READY_TIMEOUT', 0.05), \
                patch.object(wt, 'SETTLE_SECONDS', 0):
            wt.launch(self.repo, record)
        return calls

    def test_idea_opens_the_agent_with_nothing_typed(self):
        record = self.create()
        calls = self.launch(record, 'Claude Code v2\n❯ Try "edit a file"\n')
        self.assertTrue(any(x[0] == 'new-session' for x in calls))
        self.assertFalse(any(x[0] in ('send-keys', 'capture-pane') for x in calls))

    def test_readiness_and_unsent_prompt(self):
        record = self.create_issue()
        calls = self.launch(record, 'Claude Code v2\n❯ Try "edit a file"\n')
        sends = [x for x in calls if x[0] == 'send-keys']
        self.assertEqual(sends, [('send-keys', '-t', '%7', '-l', '--', record['prompt'])])
        self.assertFalse(any('C-m' in x or 'Enter' in x for x in calls))
        self.assertIn(('set-option', '-t', '$5', '@wt_path', record['path']), calls)
        launch_command = next(x[-1] for x in calls if x[0] == 'new-session')
        self.assertIn('#{session_attached}', launch_command)
        self.assertIn('"$TMUX_PANE"', launch_command)
        self.assertIn('done; exec /usr/bin/env -u NO_COLOR', launch_command)
        self.assertLess(calls.index(('set-option', '-t', '$5', 'remain-on-exit', 'on')),
                        calls.index(('wait-for', '-S', record['session'] + '-start')))

    def test_nothing_typed_while_agent_is_not_ready(self):
        record = self.create_issue()
        calls = self.launch(record, 'Claude Code\nDo you trust the files in this folder?\n❯ 1. Yes\n')
        self.assertFalse(any(x[0] == 'send-keys' for x in calls))

    def test_composer_that_gives_way_to_a_dialog_is_not_typed_into(self):
        record = self.create_issue()
        record['agent'] = 'codex'
        self.assertTrue(wt.composer_ready('codex', 'OpenAI Codex\n› Ask Codex to do anything\n'))
        calls = self.launch(record, 'OpenAI Codex\n› Ask Codex to do anything\n',
                            'Update available! 0.1 -> 0.2\n› 1. Update now\nPress enter to continue\n')
        self.assertFalse(any(x[0] == 'send-keys' for x in calls))

    def test_existing_session_does_not_receive_duplicate_prompt(self):
        record = self.create()
        def fake_tmux(*values, **kw):
            output = record['path'] if values[0] == 'show-options' else ''
            if values[0] == 'list-sessions':
                output = record['session'] + '\t$5\n'
            return subprocess.CompletedProcess(values, 0, output, '')
        with patch.object(wt, 'dependencies'), patch.object(wt, 'open_iterm'), patch.object(wt, 'tmux', side_effect=fake_tmux) as mock:
            wt.launch(self.repo, record)
        self.assertFalse(any(x.args[0] == 'send-keys' for x in mock.call_args_list))

    def test_context_api_failure_precedes_git_creation(self):
        with patch.object(wt, 'linear_context', side_effect=wt.Error('HTTP 401')):
            with self.assertRaisesRegex(wt.Error, '401'):
                self.create(idea=None, issue='TEST-22')
        self.assertEqual(len(self.repo.worktrees()), 1)
        self.assertEqual(self.repo.records(), [])


class ApiAndInputs(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('WT_AGENT', None)

    def test_agent_defaults_to_claude_without_a_terminal(self):
        self.assertEqual(wt.select_agent({}, args(), False), 'claude')

    def test_agent_precedence_flag_then_environment_then_config(self):
        config = {'agent': 'opencode'}
        with patch.object(wt.subprocess, 'run') as picker:
            self.assertEqual(wt.select_agent(config, args(), True), 'opencode')
            os.environ['WT_AGENT'] = 'codex'
            self.assertEqual(wt.select_agent(config, args(), True), 'codex')
            self.assertEqual(wt.select_agent(config, args(agent='claude'), True), 'claude')
        picker.assert_not_called()

    def test_agent_picker_selection_and_cancel(self):
        terminal = [patch.object(wt.sys.stdin, 'isatty', return_value=True),
                    patch.object(wt.sys.stdout, 'isatty', return_value=True),
                    patch.object(wt.shutil, 'which', return_value='/usr/bin/fzf')]
        for item in terminal:
            item.start()
            self.addCleanup(item.stop)
        selected = subprocess.CompletedProcess([], 0, 'codex\tCodex\n', '')
        with patch.object(wt.subprocess, 'run', return_value=selected):
            self.assertEqual(wt.select_agent({}, args(), True), 'codex')
        with patch.object(wt.subprocess, 'run', return_value=subprocess.CompletedProcess([], 130, '', '')):
            with self.assertRaises(KeyboardInterrupt):
                wt.select_agent({}, args(), True)

    def interactive(self):
        for item in (patch.object(wt.sys.stdin, 'isatty', return_value=True),
                     patch.object(wt.sys.stdout, 'isatty', return_value=True),
                     patch.object(wt.shutil, 'which', return_value='/usr/bin/fzf')):
            item.start()
            self.addCleanup(item.stop)

    def test_claude_model_and_effort_default_to_claude_codes_own(self):
        self.assertEqual(wt.select_claude_options({}, args(), 'claude', False), (None, None))
        with patch.object(wt.shutil, 'which', side_effect=lambda name: '/bin/' + name):
            command = wt.agent_command({'path': '/tmp/example', 'mode': 'issue', 'agent': 'claude', 'model': None, 'effort': None})
        self.assertEqual(command[-1], '/bin/claude')

    def test_claude_model_and_effort_pickers(self):
        self.interactive()
        picks = [subprocess.CompletedProcess([], 0, 'opus\tOpus\n', ''),
                 subprocess.CompletedProcess([], 0, 'high\thigh\n', '')]
        with patch.object(wt.subprocess, 'run', side_effect=picks) as picker:
            self.assertEqual(wt.select_claude_options({}, args(), 'claude', True), ('opus', 'high'))
        self.assertIn('--prompt=model > ', picker.call_args_list[0].args[0])
        self.assertIn('--prompt=effort > ', picker.call_args_list[1].args[0])
        keep = subprocess.CompletedProcess([], 0, 'default\tClaude Code default\n', '')
        with patch.object(wt.subprocess, 'run', return_value=keep):
            self.assertEqual(wt.select_claude_options({}, args(), 'claude', True), (None, None))
        with patch.object(wt.subprocess, 'run', return_value=subprocess.CompletedProcess([], 130, '', '')):
            with self.assertRaises(KeyboardInterrupt):
                wt.select_claude_options({}, args(), 'claude', True)

    def test_claude_flags_and_config_skip_the_pickers(self):
        self.interactive()
        config = {'claude': {'model': 'sonnet', 'effort': 'low'}}
        with patch.object(wt.subprocess, 'run') as picker:
            self.assertEqual(wt.select_claude_options(config, args(), 'claude', True), ('sonnet', 'low'))
            self.assertEqual(wt.select_claude_options(config, args(model='claude-opus-5-5', effort='max'), 'claude', True),
                             ('claude-opus-5-5', 'max'))
        picker.assert_not_called()
        with patch.object(wt.shutil, 'which', side_effect=lambda name: '/bin/' + name):
            command = wt.agent_command({'path': '/tmp/example', 'mode': 'issue', 'agent': 'claude', 'model': 'sonnet', 'effort': 'low'})
        self.assertEqual(command[-5:], ['/bin/claude', '--model', 'sonnet', '--effort', 'low'])

    def test_claude_options_are_validated_and_claude_only(self):
        with self.assertRaisesRegex(wt.Error, 'effort'):
            wt.select_claude_options({}, args(effort='extreme'), 'claude', False)
        with self.assertRaisesRegex(wt.Error, 'model'):
            wt.select_claude_options({}, args(model='opus; rm -rf'), 'claude', False)
        with self.assertRaisesRegex(wt.Error, 'claude only'):
            wt.select_claude_options({}, args(model='opus'), 'codex', False)
        with patch.object(wt.subprocess, 'run') as picker:
            self.assertEqual(wt.select_claude_options({'claude': {'model': 'opus'}}, args(), 'codex', True), (None, None))
        picker.assert_not_called()

    def test_unknown_agent_rejected_before_creation(self):
        with self.assertRaisesRegex(wt.Error, 'Unknown agent'):
            wt.select_agent({}, args(agent='not-an-agent'), False)

    def test_agent_commands(self):
        with patch.object(wt.shutil, 'which', side_effect=lambda name: '/bin/' + name):
            claude = wt.agent_command({'path': '/tmp/example', 'mode': 'issue', 'agent': 'claude'})
            codex = wt.agent_command({'path': '/tmp/example', 'mode': 'pr', 'agent': 'codex'})
            legacy = wt.agent_command({'path': '/tmp/example', 'mode': 'idea'})
        self.assertEqual(claude, ['/usr/bin/env', '-u', 'NO_COLOR', '/bin/claude'])
        self.assertEqual(codex, ['/usr/bin/env', '-u', 'NO_COLOR', '/bin/codex', '-C', '/tmp/example', '--sandbox', 'read-only'])
        self.assertEqual(legacy[-1], '/bin/claude')

    def test_dns_and_auth_errors_are_actionable_without_secret_leaks(self):
        opener = MagicMock()
        opener.open.side_effect = wt.urllib.error.URLError(wt.socket.gaierror(8, 'unavailable'))
        with patch.object(wt.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(wt.Error, 'DNS lookup failed'):
                wt.request_json('https://api.linear.app/graphql', {'Authorization': 'private'}, data={})
        opener.open.side_effect = wt.urllib.error.HTTPError('https://api.linear.app', 401, 'unauthorized', {}, None)
        with patch.object(wt.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(wt.Error) as caught:
                wt.request_json('https://api.linear.app/graphql', {'Authorization': 'private'}, data={})
        self.assertIn('HTTP 401', str(caught.exception))
        self.assertNotIn('private', str(caught.exception))

    def test_linear_key_is_required_and_sent_only_as_a_header(self):
        os.environ.pop('LINEAR_API_KEY', None)
        with self.assertRaisesRegex(wt.Error, 'LINEAR_API_KEY'):
            wt.linear_query({}, 'query{viewer{name}}')
        os.environ['MY_LINEAR'] = 'private'
        with patch.object(wt, 'request_json', return_value=({'data': {'viewer': {'name': 'me'}}}, {})) as request:
            data = wt.linear_query({'linear': {'token_env': 'MY_LINEAR'}}, 'query{viewer{name}}')
        self.assertEqual(data, {'viewer': {'name': 'me'}})
        self.assertEqual(request.call_args.args[:2], (wt.LINEAR_API_URL, {'Authorization': 'private'}))
        self.assertNotIn('private', str(request.call_args.kwargs['data']))

    def test_linear_graphql_errors_are_reported(self):
        os.environ['LINEAR_API_KEY'] = 'private'
        with patch.object(wt, 'request_json', return_value=({'errors': [{'message': 'Bad\nquery'}]}, {})):
            with self.assertRaisesRegex(wt.Error, 'Linear API error: Bad query'):
                wt.linear_query({}, 'query{nope}')

    def test_linear_tickets_and_context(self):
        nodes = [{'identifier': 'ORA-1', 'title': 'First'}, {'identifier': 'ORA-2', 'title': 'Second'}]
        with patch.object(wt, 'linear_query', return_value={'viewer': {'assignedIssues': {'nodes': nodes}}}):
            self.assertEqual([x['identifier'] for x in wt.linear_tickets({})], ['ORA-1', 'ORA-2'])
        issue = {'identifier': 'ORA-38', 'title': 'Title', 'url': 'https://linear.app/orare/issue/ORA-38',
                 'description': 'First\nSecond \\"quoted\\"'}
        with patch.object(wt, 'linear_query', return_value={'issues': {'nodes': [issue]}}) as query:
            title, text = wt.linear_context({}, 'ORA-38')
        self.assertEqual(query.call_args.args[2], {'key': 'ORA', 'num': 38})
        self.assertEqual(title, 'Title')
        self.assertTrue(text.startswith('# ORA-38: Title\n\nhttps://linear.app/orare/issue/ORA-38\n'))
        self.assertIn('First\nSecond \\"quoted\\"', text)
        with patch.object(wt, 'linear_query', return_value={'issues': {'nodes': []}}):
            with self.assertRaisesRegex(wt.Error, 'not found in Linear'):
                wt.linear_context({}, 'ORA-999')

    def test_github_wraps_gh_and_reports_its_failures(self):
        repo = MagicMock(main=Path('/tmp/example'))
        with patch.object(wt.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(wt.Error, 'brew install gh'):
                wt.GitHub(repo)
        with patch.object(wt.shutil, 'which', return_value='/bin/gh'):
            api = wt.GitHub(repo)
        ok = subprocess.CompletedProcess([], 0, '[{"state": "MERGED", "headRefOid": "abc"}]', '')
        with patch.object(wt, 'run', return_value=ok) as run:
            self.assertEqual(api.merged_prs('feature/x')[0]['headRefOid'], 'abc')
        self.assertEqual(run.call_args.args[0][:6], ['gh', 'pr', 'list', '--state', 'merged', '--head'])
        self.assertEqual(run.call_args.kwargs['cwd'], repo.main)
        failed = subprocess.CompletedProcess([], 1, '', 'no pull requests found\n')
        with patch.object(wt, 'run', return_value=failed):
            with self.assertRaisesRegex(wt.Error, 'GitHub: no pull requests found'):
                api.pr('7')

    def test_composer_readiness_per_agent(self):
        self.assertTrue(wt.composer_ready('claude', ' Claude Code v2.1\n────\n❯ Try "edit a file"'))
        self.assertFalse(wt.composer_ready('claude', 'Claude Code\nDo you trust the files in this folder?\n❯ 1. Yes'))
        self.assertFalse(wt.composer_ready('claude', 'zsh\n❯ user prompt'))
        self.assertTrue(wt.composer_ready('opencode', '┃  Ask anything... "Fix broken tests"\n┃  Build · GLM'))
        self.assertFalse(wt.composer_ready('opencode', ''))
        self.assertTrue(wt.composer_ready('codex', 'OpenAI Codex\n› Ask anything'))
        self.assertFalse(wt.composer_ready('codex', 'Codex\nUpdate available! 0.1 -> 0.2\n› 1. Update now'))
        self.assertFalse(wt.composer_ready('codex', 'OpenAI Codex\nSign in\n› Continue'))
        self.assertFalse(wt.composer_ready('codex', 'OpenAI Codex\nmodel:     loading\n› Ask Codex to do anything'))
        self.assertFalse(wt.composer_ready('codex', 'zsh\n› user prompt'))

    def test_slug_and_picker_control_characters(self):
        self.assertEqual(wt.slug('name; $(touch /tmp/pwned) "test"'), 'name-touch-tmp-pwned-test')
        self.assertEqual(wt.row_text('Title\nnew\trow\x1b'), 'Title new row')
        with self.assertRaises(wt.Error):
            wt.slug('...')


if __name__ == '__main__':
    unittest.main()
