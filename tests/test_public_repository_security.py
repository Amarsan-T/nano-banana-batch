"""Offline publication safeguards; never attach to Chrome or contact Flow."""

import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
SYNTHETIC_UUID_PATTERN = re.compile(r"00000000-0000-4000-8000-[0-9a-f]{12}")
PERSONAL_PATH_PATTERN = re.compile(
    r"[a-z]:[\\/]+Users[\\/]+|/(?:Users|home)/[\w.-]+/", re.IGNORECASE
)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"ya29\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}"),
)


class PublicRepositorySecurityTests(unittest.TestCase):
    def git(self, *arguments, paths=None):
        executable = shutil.which("git")
        if not executable or not (ROOT / ".git").exists():
            self.skipTest("Publication checks require Git and a repository checkout.")
        result = subprocess.run(
            [executable, *arguments],
            cwd=ROOT,
            input=None if paths is None else ("\0".join(paths) + "\0").encode("utf-8"),
            capture_output=True,
            timeout=10,
        )
        result.stdout = result.stdout.decode("utf-8")
        self.assertIn(result.returncode, (0, 1), "Read-only Git check failed.")
        return result

    def test_sensitive_runtime_paths_are_ignored(self):
        paths = (
            ".flow_chrome_profile/Default/Network/Cookies",
            ".flow_chrome_profile_backup/Default/Preferences",
            ".flow_chrome_profile-backup.zip",
            ".browser_profile_backup/session.json",
            "chrome_profile_copy/Local State",
            "profile_backups/backup.zip",
            "profile-backup.zip",
            "User Data/Default/Cookies",
            "Cookies", "Login Data", "Local State", "Secure Preferences",
            "runs/custom batch/run_state.json", "runs/custom batch/assets/image.jpeg",
            "assets/image.png", "output/image.webp", "outputs/image.png",
            "downloads/file.jpeg", "staging/file", "download_staging/file",
            "run_state.json", "run_state.json.backup", "image.crdownload",
            "config.json", "config.local.json", "config-private.json",
            "config.example.json.backup", "private_configs/settings.json",
            "settings.private.json", "settings.local.json",
            "settings.private.yaml", "settings.local.toml", "config_private.ini",
            "prompts.txt", "prompts_edited.txt", "prompts.txt.backup",
            "prompt.txt", "prompt_custom.json", "private.prompt",
            "prompts.private.json", "prompts/list.txt", "prompt_backups/list.txt",
            ".env", ".env.local", "secrets/key.txt", "secret.json",
            "credentials.json", "client_secret.json", "service-account.json",
            "api_keys.json", "cookies.json", "auth.json", "storage_state.json",
            "storage-state.json", "session.json", "token.json", "private.pem",
            "certificate.pfx", "browser.sqlite", "cookie.db", ".netrc",
            ".aws/credentials", ".ssh/id_ed25519",
            ".venv/pyvenv.cfg", ".venv_backup/pyvenv.cfg", "venv/pyvenv.cfg",
            "nano_batch/__pycache__/state.cpython-312.pyc", ".pytest_cache/README.md",
            "logs/batch.log", "batch.log", "diagnostics/flow_inspect.png",
            "batch.log.1", "token.txt",
            "diagnostics_backup/trace.json", "traces/trace.json", "capture.har",
            "tmp/file.txt", "temp/file.txt", "config.json.tmp", "file.bak",
            "backups/private.zip", ".codex/settings.json", ".vscode/settings.json",
        )
        result = self.git("check-ignore", "--no-index", "--stdin", "-z", paths=paths)
        self.assertEqual(set(result.stdout.rstrip("\0").split("\0")), set(paths))

    def test_public_source_tests_and_examples_are_not_ignored(self):
        paths = [
            ".gitignore", "AGENTS.md", "README.md", "app.py", "run.bat",
            "requirements.txt", "config.example.json",
        ]
        for directory, pattern in (("nano_batch", "*.py"), ("tests", "*.py"), ("data", "*.json")):
            paths.extend(path.relative_to(ROOT).as_posix() for path in (ROOT / directory).rglob(pattern))
        result = self.git("check-ignore", "--no-index", "--stdin", "-z", paths=paths)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_commit_eligible_text_has_no_personal_paths_or_recognizable_secrets(self):
        result = self.git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        self.assertEqual(result.returncode, 0)
        paths = sorted(set(result.stdout.rstrip("\0").split("\0")))
        self.assertIn("config.example.json", paths)
        for relative_path in paths:
            with self.subTest(file=relative_path):
                raw = (ROOT / relative_path).read_bytes()
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    self.fail("Review binary artifacts separately before publication.")
                # Boolean assertions deliberately avoid echoing sensitive matches.
                self.assertFalse(PERSONAL_PATH_PATTERN.search(text), "Personal home path found.")
                for pattern in SECRET_PATTERNS:
                    self.assertFalse(pattern.search(text), "Recognizable secret pattern found.")

    def test_documentation_and_fixtures_use_only_synthetic_literal_uuids(self):
        paths = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "config.example.json"]
        paths.extend((ROOT / "tests").rglob("*.py"))
        paths.extend((ROOT / "data").rglob("*.json"))
        for path in paths:
            with self.subTest(file=path.relative_to(ROOT).as_posix()):
                uuids = UUID_PATTERN.findall(path.read_text(encoding="utf-8"))
                self.assertTrue(
                    all(SYNTHETIC_UUID_PATTERN.fullmatch(value) for value in uuids),
                    "Non-synthetic literal UUID found; review its provenance.",
                )


if __name__ == "__main__":
    unittest.main()
