"""Real Git fixtures and deterministic validation; no model/network calls."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from release import ReleaseAssistant, ReleaseError, evidence, resolve, validate_plan


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        self.write('README.md', '# Accounts\nLogin returns metadata.\n')
        self.write('src/auth.py', 'def login():\n    return False\n')
        self.base = self.commit('Initial account API')
        self.write('src/auth.py', 'def login():\n    return True\n')
        self.head = self.commit('Fix authentication result')
        self.assistant = ReleaseAssistant(self.root, mode='demo', exports=Path(self.temp.name) / 'exports')

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self, message):
        self.git('add', '-A')
        self.git('commit', '-qm', message)
        return self.git('rev-parse', 'HEAD')

    def valid_plan(self):
        return {'summary': 'Исправление результата входа.',
                'highlights': [{'text': 'Исправлен возвращаемый результат login.', 'commits': [self.head]}],
                'risks': [], 'checklist': ['Проверить успешный и ошибочный вход.']}


class ReleaseTests(GitFixture):
    def test_git_evidence_pins_sha_and_includes_real_diff(self):
        data = evidence(self.root, self.base, 'HEAD')
        self.assertEqual(data['head'], self.head)
        self.assertEqual(data['commits'], [{'sha': self.head, 'subject': 'Fix authentication result'}])
        self.assertIn('+    return True', data['diff'])
        self.assertEqual(data['files'], ['src/auth.py'])
        self.assertEqual(data['coverage'], 'complete')

    def test_no_code_execution_or_worktree_changes(self):
        self.write('src/auth.py', 'raise RuntimeError("DO NOT EXECUTE")\n')
        result = self.assistant.prepare(self.base, self.head, 'Подготовь релиз')
        self.assertIn('return True', result['evidence']['diff'])
        self.assertIn('DO NOT EXECUTE', (self.root / 'src/auth.py').read_text())
        self.assertFalse(self.assistant.exports.exists())

    def test_secrets_are_excluded_even_when_removed(self):
        self.write('.env', 'DEEPSEEK_API_KEY=sk-' + 'a' * 30)
        self.write('config.py', 'password = "fixture-private-value"\n')
        secret_head = self.commit('Add sensitive files')
        self.write('config.py', 'enabled = True\n')
        final = self.commit('Remove sensitive assignment')
        data = evidence(self.root, secret_head, final)
        self.assertNotIn('fixture-private-value', json.dumps(data))
        self.assertEqual(data['coverage'], 'partial')
        self.assertEqual(data['excluded_files'][0]['path'], 'config.py')
        data = evidence(self.root, self.base, secret_head)
        self.assertNotIn('sk-' + 'a' * 30, json.dumps(data))
        self.assertEqual(len(data['excluded_files']), 2)

    def test_options_invalid_and_nonancestor_refs_rejected(self):
        for ref in ('--help', 'HEAD;echo', '../outside:foo', '', None):
            with self.subTest(ref=ref), self.assertRaises(ReleaseError):
                resolve(self.root, ref)
        with self.assertRaises(ReleaseError) as caught:
            evidence(self.root, self.head, self.base)
        self.assertEqual(caught.exception.code, 'not_ancestor')
        with self.assertRaises(ReleaseError):
            evidence(self.root, self.head, self.head)

    def test_literal_git_filename_cannot_include_excluded_file(self):
        self.write(':(glob)*.md', 'Ordinary release notes\n')
        self.write('secrets.md', 'CONFIDENTIAL SYNTHETIC CUSTOMER DATA\n')
        head = self.commit('Add oddly named Markdown and excluded data')
        data = evidence(self.root, self.head, head)
        self.assertNotIn('CONFIDENTIAL SYNTHETIC CUSTOMER DATA', data['diff'])
        self.assertIn(':(glob)*.md', data['included_files'])
        self.assertIn('secrets.md', [entry['path'] for entry in data['excluded_files']])

    def test_limits_fail_instead_of_truncating(self):
        with patch('release.MAX_COMMITS', 0), self.assertRaises(ReleaseError):
            evidence(self.root, self.base, self.head)
        with patch('release.MAX_FILES', 0), self.assertRaises(ReleaseError):
            evidence(self.root, self.base, self.head)
        with patch('release.MAX_DIFF', 10), self.assertRaises(ReleaseError):
            evidence(self.root, self.base, self.head)

    def test_rejects_hallucinated_citations_and_invalid_ai_schema(self):
        data = evidence(self.root, self.base, self.head)
        plan = self.valid_plan()
        self.assertEqual(validate_plan(plan, data), plan)
        plan['highlights'][0]['commits'] = ['0' * 40]
        with self.assertRaises(ReleaseError) as caught:
            validate_plan(plan, data)
        self.assertEqual(caught.exception.code, 'invalid_citation')
        for invalid in ([], {}, {**self.valid_plan(), 'checklist': []}, {**self.valid_plan(), 'extra': 42}):
            with self.subTest(invalid=invalid), self.assertRaises(ReleaseError):
                validate_plan(invalid, data)

    @patch('release.llm.ask')
    def test_cloud_uses_actual_evidence_and_deepseek_provider(self, ask):
        ask.return_value = SimpleNamespace(text=json.dumps(self.valid_plan()))
        assistant = ReleaseAssistant(self.root, mode='cloud')
        result = assistant.prepare(self.base, self.head, 'Объясни изменения пользователям')
        from release import llm
        self.assertIs(ask.call_args.kwargs['provider'], llm.DEEPSEEK)
        self.assertTrue(ask.call_args.kwargs['json_mode'])
        self.assertIn(self.head, ask.call_args.args[0])
        self.assertIn('return True', ask.call_args.args[0])
        self.assertIn('DeepSeek AI', result['markdown'])

    def test_export_is_exact_idempotent_and_conflict_safe(self):
        result = self.assistant.prepare(self.base, self.head, 'Подготовь релиз')
        saved = self.assistant.save(result)
        self.assertEqual(Path(saved['path']).read_text(), result['markdown'])
        self.assertEqual(self.assistant.save(result), saved)
        Path(saved['path']).write_text('external')
        with self.assertRaises(ReleaseError):
            self.assistant.save(result)
        self.assertEqual(Path(saved['path']).read_text(), 'external')
        with self.assertRaises(ReleaseError):
            self.assistant.save({**result, 'id': '../../outside'})


if __name__ == '__main__':
    unittest.main()
