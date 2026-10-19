"""Real workspace/MCP tests; synthetic temporary project only."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "shared"))
from mcp import MCPClient
from workspace import Workspace, FileError


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        shutil.copytree(HERE / "sample", self.root)
        self.workspace = Workspace(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_read_and_multi_file_search(self):
        found = self.workspace.search_text("login")
        paths = {m["path"] for m in found["matches"]}
        self.assertTrue({"src/auth.py", "src/web.py", "src/cli.py"} <= paths)
        self.assertIn("3600", self.workspace.read_file("src/auth.py")["content"])
        self.assertFalse(found["truncated"])

    def test_preview_does_not_write_and_apply_creates_file(self):
        self.workspace.propose_file("reports/usages.md", "# Uses\n")
        self.assertFalse((self.root / "reports").exists())
        self.assertIn("+# Uses", self.workspace.show_diff()["diff"])
        with self.assertRaises(FileError):
            self.workspace.apply_changes()
        permitted = Workspace(self.root, allow_apply=True)
        permitted.propose_file("reports/usages.md", "# Uses\n")
        result = permitted.apply_changes()
        self.assertEqual(result["applied_files"], ["reports/usages.md"])
        self.assertEqual((self.root / "reports/usages.md").read_text(), "# Uses\n")

    def test_existing_file_must_be_read_before_proposal(self):
        with self.assertRaises(FileError):
            self.workspace.propose_file("docs/auth.md", "changed")
        self.workspace.read_file("docs/auth.md")
        self.workspace.propose_file("docs/auth.md", "changed")
        self.assertIn("-# Авторизация", self.workspace.show_diff()["diff"])

    def test_conflict_is_checked_before_any_write(self):
        workspace = Workspace(self.root, allow_apply=True)
        workspace.read_file("docs/auth.md")
        workspace.propose_file("reports/new.md", "new")
        workspace.propose_file("docs/auth.md", "replacement")
        (self.root / "docs/auth.md").write_text("external change")
        with self.assertRaises(FileError) as caught:
            workspace.apply_changes()
        self.assertEqual(caught.exception.code, "conflict")
        self.assertFalse((self.root / "reports/new.md").exists())
        self.assertEqual((self.root / "docs/auth.md").read_text(), "external change")

    def test_rejects_protected_paths_symlinks_and_source_writes(self):
        (self.root / "link.md").symlink_to(self.root / "README.md")
        for path in ("../other.md", "/etc/passwd", ".env", ".git/config", "link.md", "src//auth.py", "src/./auth.py"):
            with self.subTest(path=path), self.assertRaises(FileError):
                self.workspace.read_file(path)
        for path in ("src/auth.py", "../other.md", "docs/.env.md"):
            with self.subTest(path=path), self.assertRaises(FileError):
                self.workspace.propose_file(path, "new content")

    def test_secrets_and_binary_files_never_returned(self):
        (self.root / "src/config.py").write_text('api_key = "fake-test-value"')
        (self.root / "src/binary.py").write_bytes(b"\0invalid")
        for path in ("src/config.py", "src/binary.py"):
            with self.assertRaises(FileError):
                self.workspace.read_file(path)
        result = self.workspace.search_text("fake-test")
        self.assertEqual(result["matches"], [])
        self.assertGreaterEqual(result["skipped"], 2)

    def test_generated_report_is_not_recursively_indexed(self):
        (self.root / "reports").mkdir()
        (self.root / "reports/old.md").write_text("login GENERATED")
        result = self.workspace.search_text("GENERATED")
        self.assertEqual(result["matches"], [])

    def test_real_stdio_mcp_read_and_preview(self):
        with MCPClient([sys.executable, str(HERE / "server.py"), "--project", str(self.root)]) as client:
            names = {tool.name for tool in client.list_tools()}
            self.assertIn("propose_file", names)
            self.assertNotIn("apply_changes", names)
            read = client.call_tool("read_file", {"path": "src/auth.py"})
            data = json.loads(read["content"][0]["text"])
            self.assertIn("expires_in", data["content"])
            client.call_tool("propose_file", {"path": "reports/demo.md", "content": "# Demo\n"})
            diff = client.call_tool("show_diff", {})
            self.assertIn("+# Demo", json.loads(diff["content"][0]["text"])["diff"])
            denied = client.call_tool("apply_changes", {})
            self.assertTrue(denied["isError"])
        self.assertFalse((self.root / "reports").exists())


if __name__ == "__main__":
    unittest.main()
