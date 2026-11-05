"""
Tests for low-level NT handle management and directory step traversal (Stage 7).
Strictly conforms to PROJECT_SPEC.md v1.1.5 section 7.2.
"""

import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

from core.exceptions import (
    FilesystemError,
    ReparsePointError,
    VolumeMismatchError,
    SharingViolationError,
)
from core.nt_handle import (
    SafeNTHandle,
    open_directory_handle,
    step_traverse_directories,
    move_file_replace_if_exists_false,
)


class TestPathSecurityHandles(unittest.TestCase):
    """Unit tests for low-level handle security and directory traversal."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_nt_handle_")
        self.game_root = os.path.join(self.temp_dir, "The Sims 4")
        os.makedirs(self.game_root, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_safe_handle_lifecycle(self):
        """Test SafeNTHandle creation, validity check, and context manager closure."""
        with open_directory_handle(self.game_root) as h:
            self.assertTrue(h.is_valid())
            self.assertEqual(h.path, os.path.abspath(self.game_root))
            self.assertTrue(h.is_dir)

        # After exiting with block, handle must be closed and invalid
        self.assertFalse(h.is_valid())
        self.assertEqual(h.handle, 0)

    def test_reparse_point_rejection(self):
        """
        Verify that Symlinks / Reparse Points are detected and rejected with ReparsePointError
        per Section 7.2.
        """
        symlink_dir = os.path.join(self.temp_dir, "symlink_folder")
        target_dir = os.path.join(self.temp_dir, "real_folder")
        os.makedirs(target_dir, exist_ok=True)

        try:
            os.symlink(target_dir, symlink_dir)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks not supported in current environment.")

        with self.assertRaises(ReparsePointError):
            open_directory_handle(symlink_dir)

    def test_step_traverse_directories(self):
        """
        Test step_traverse_directories: step-by-step traversal to parent folder,
        creating intermediate subdirectories safely.
        """
        rel_path = "EP01/Data/Client/ClientFullBuild0.package"
        parent_handle = step_traverse_directories(self.game_root, rel_path, create_missing=True)

        try:
            self.assertTrue(parent_handle.is_valid())
            expected_parent = os.path.join(self.game_root, "EP01", "Data", "Client")
            self.assertEqual(parent_handle.path, os.path.abspath(expected_parent))
            self.assertTrue(os.path.isdir(expected_parent))
        finally:
            parent_handle.close()

    def test_move_file_replace_if_exists_false(self):
        """
        Verify ReplaceIfExists = FALSE semantics:
        1. Moves file when destination does not exist.
        2. Raises FileExistsError when destination already exists (refusing overwrite).
        """
        src = os.path.join(self.temp_dir, "source.txt")
        dst = os.path.join(self.temp_dir, "dest.txt")

        with open(src, "w") as f:
            f.write("content to move")

        # First move: destination does not exist -> must succeed
        move_file_replace_if_exists_false(src, dst)
        self.assertFalse(os.path.exists(src))
        self.assertTrue(os.path.exists(dst))

        # Re-create source: destination already exists -> must fail with FileExistsError
        with open(src, "w") as f:
            f.write("second content")

        with self.assertRaises(FileExistsError):
            move_file_replace_if_exists_false(src, dst)

        # Confirm destination was NOT overwritten
        with open(dst, "r") as f:
            self.assertEqual(f.read(), "content to move")


if __name__ == "__main__":
    unittest.main()
