"""
Pytest configuration, fixtures, and sandbox isolation for test suite.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class IsolatedEnvironmentTestCase(unittest.TestCase):
    """
    Base test case providing an isolated temporary directory sandbox
    simulating %LOCALAPPDATA% and scratch storage.
    """
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="s4_test_env_")
        self.appdata_dir = os.path.join(self.test_dir, "LocalAppData")
        os.makedirs(self.appdata_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
