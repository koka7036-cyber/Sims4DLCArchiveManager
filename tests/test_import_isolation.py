"""
AST-based Import Isolation Test.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 4.1 and 12.2:
Verifies zero leakage of owner_tools administrative modules into the customer application code.
"""

import ast
import os
import unittest
from pathlib import Path


class TestImportIsolation(unittest.TestCase):
    """
    Scans Abstract Syntax Trees (AST) of all client source files to verify that
    none of them import or depend on owner_tools or owner-only credentials.
    """

    def setUp(self):
        self.root_dir = Path(".")
        self.client_dirs = ["core", "database", "branding", "gui"]
        self.client_files = ["main.py"]

    def _collect_client_py_files(self):
        files = []
        for d in self.client_dirs:
            p = self.root_dir / d
            if p.is_dir():
                for root, _, filenames in os.walk(p):
                    for fn in filenames:
                        if fn.endswith(".py"):
                            files.append(Path(root) / fn)
        for f in self.client_files:
            fp = self.root_dir / f
            if fp.is_file():
                files.append(fp)
        return files

    def test_zero_owner_tools_imports_in_client_code(self):
        """
        Parses AST of all client modules and asserts that no import from owner_tools exists.
        """
        client_files = self._collect_client_py_files()
        self.assertGreater(len(client_files), 15, "Expected to find client Python modules.")

        violations = []

        for py_path in client_files:
            with open(py_path, "r", encoding="utf-8") as f:
                source = f.read()

            try:
                tree = ast.parse(source, filename=str(py_path))
            except SyntaxError as e:
                self.fail(f"Syntax error in {py_path}: {e}")

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "owner_tools" or alias.name.startswith("owner_tools."):
                            violations.append((str(py_path), node.lineno, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    if node.module and (node.module == "owner_tools" or node.module.startswith("owner_tools.")):
                        violations.append((str(py_path), node.lineno, node.module))

        if violations:
            err_msg = "\n".join(f"  {path}:{lineno} imports '{mod}'" for path, lineno, mod in violations)
            self.fail(f"AST Isolation Violation! Customer modules must never import owner_tools:\n{err_msg}")

    def test_zero_raw_private_key_literals_in_client_code(self):
        """
        Scans client source files for any hardcoded private key strings or certificates.
        """
        client_files = self._collect_client_py_files()
        forbidden_substrings = [
            "-----BEGIN PRIVATE KEY-----",
            "-----BEGIN ED25519 PRIVATE KEY-----",
            "ed25519_master.key",
            "ed25519_master",
        ]

        violations = []
        for py_path in client_files:
            with open(py_path, "r", encoding="utf-8") as f:
                content = f.read()
            for sub in forbidden_substrings:
                if sub in content:
                    violations.append((str(py_path), sub))

        self.assertEqual(violations, [], f"Forbidden private key references found in client code: {violations}")


if __name__ == "__main__":
    unittest.main()
