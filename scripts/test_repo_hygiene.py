"""Prevent private configuration and credential files entering the public repo."""
import fnmatch
from pathlib import Path, PurePosixPath
import subprocess
import unittest

import council_install


def private_file(name):
    base = PurePosixPath(name).name.lower()
    return any(fnmatch.fnmatchcase(base, pattern) for pattern in ('*.local.yaml', '*.local.yml', '*.key', '.env*'))


class Hygiene(unittest.TestCase):
    def test_no_private_files_tracked(self):
        root = Path(__file__).resolve().parent.parent
        try:
            result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            self.skipTest('git is unavailable')
        if result.returncode:
            self.skipTest('git work tree is unavailable')
        names = result.stdout.decode('utf-8').split('\0')
        self.assertEqual([name for name in names if private_file(name)], [])

    def test_installer_allowlist_excludes_private_files(self):
        self.assertEqual([name for name in council_install.FILES if private_file(name)], [])


if __name__ == '__main__':
    unittest.main()
