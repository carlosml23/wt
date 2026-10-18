from pathlib import Path
import sys
from unittest.mock import patch

from test_wt import Worktrees, wt


class Bootstrap(Worktrees):
    def hook(self, source):
        self.repo.settings['bootstrap'] = [sys.executable, '-c', source]

    def test_create_runs_hook_in_worktree(self):
        self.hook('from pathlib import Path; Path("bootstrap-marker").write_text("ok")')
        record = self.create()
        self.assertEqual(record['setup_status'], 'ready')
        self.assertTrue((Path(record['path']) / 'bootstrap-marker').exists())
        self.assertFalse((self.main / 'bootstrap-marker').exists())

    def test_failure_preserves_checkout_and_blocks_launch_until_retry(self):
        self.hook('raise SystemExit(7)')
        with self.assertRaisesRegex(wt.Error, 'Setup failed'):
            self.create()
        record = self.repo.records()[0]
        self.assertEqual(record['setup_status'], 'failed')
        self.assertTrue(Path(record['path']).is_dir())
        with self.assertRaisesRegex(wt.Error, 'incomplete'):
            wt.launch(self.repo, record)
        self.hook('pass')
        wt.setup_worktree(self.repo, record)
        self.assertEqual(record['setup_status'], 'ready')

    def test_skip_setup_does_not_execute(self):
        self.hook('raise SystemExit(7)')
        self.assertEqual(self.create(skip_setup=True)['setup_status'], 'skipped')

    def test_pr_needs_explicit_trust(self):
        record = self.create()
        record['mode'] = 'pr'
        self.hook('pass')
        with patch.object(wt.sys.stdin, 'isatty', return_value=False):
            wt.setup_worktree(self.repo, record)
        self.assertEqual(record['setup_status'], 'skipped')
        wt.setup_worktree(self.repo, record, trust=True)
        self.assertEqual(record['setup_status'], 'ready')

    def test_invalid_hook_fails_without_execution(self):
        record = self.create()
        self.repo.settings['bootstrap'] = 'npm install'
        with self.assertRaisesRegex(wt.Error, 'argument list'):
            wt.setup_worktree(self.repo, record)
