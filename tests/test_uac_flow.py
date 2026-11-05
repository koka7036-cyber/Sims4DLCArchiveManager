"""
Unit tests for LockContext and exclusive file locking (Stage 1 scope).
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 7.6.
"""

import os
import time
import threading
import unittest

from core.security import (
    acquire_lock_file,
    generate_lease_token,
    LockContext,
)
from core.exceptions import (
    SharingViolationError,
    LockLostException,
    LeaseExpiredError,
)
from tests.conftest import IsolatedEnvironmentTestCase


class TestUACFlowStage1(IsolatedEnvironmentTestCase):

    def test_lock_context_structure(self):
        """Verify that LockContext contains all mandatory fields."""
        tx_id = "test_tx_001"
        lease_token = generate_lease_token()
        ctx = LockContext(
            tx_id=tx_id,
            lease_token=lease_token,
            lock_handle=1001,
            expected_lock_file_id=424242,
            expected_lock_volume_serial=999999
        )

        self.assertEqual(ctx.tx_id, tx_id)
        self.assertEqual(ctx.lease_token, lease_token)
        self.assertEqual(ctx.lock_handle, 1001)
        self.assertEqual(ctx.expected_lock_file_id, 424242)
        self.assertEqual(ctx.expected_lock_volume_serial, 999999)
        self.assertIsInstance(ctx.lock_sync, type(threading.RLock()))
        self.assertTrue(ctx.is_active)

    def test_exclusive_file_locking_violation(self):
        """
        Verify that opening the same lock file again raises SharingViolationError
        (dwShareMode = 0 exclusivity contract).
        """
        lock_path = os.path.join(self.test_dir, "locks", "tx_test_tx_002.lock")
        tx_id = "test_tx_002"
        token1 = generate_lease_token()
        token2 = generate_lease_token()

        # First acquisition succeeds
        ctx1 = acquire_lock_file(lock_path, tx_id, token1)
        self.assertTrue(ctx1.is_active)

        try:
            # Second acquisition on the same path must fail with SharingViolationError
            with self.assertRaises(SharingViolationError):
                acquire_lock_file(lock_path, tx_id, token2)
        finally:
            ctx1.close()

        # After closing, acquisition succeeds again
        ctx2 = acquire_lock_file(lock_path, tx_id, token2)
        try:
            self.assertTrue(ctx2.is_active)
        finally:
            ctx2.close()

    def test_continuous_lock_sync_guard(self):
        """
        Verify that inside guard(), lock_sync is continuously held by the active thread
        and cannot be acquired by another thread without blocking.
        """
        lock_path = os.path.join(self.test_dir, "locks", "tx_test_tx_003.lock")
        tx_id = "test_tx_003"
        token = generate_lease_token()
        ctx = acquire_lock_file(lock_path, tx_id, token)

        try:
            thread_acquired = []

            def worker():
                # Attempt non-blocking acquire
                res = ctx.lock_sync.acquire(blocking=False)
                if res:
                    thread_acquired.append(True)
                    ctx.lock_sync.release()
                else:
                    thread_acquired.append(False)

            with ctx.guard():
                # Launch worker while main thread is inside guard
                t = threading.Thread(target=worker)
                t.start()
                t.join()

                # Other thread must NOT have acquired lock_sync
                self.assertEqual(thread_acquired, [False])

            # Outside guard, worker can acquire lock_sync
            thread_acquired.clear()
            t2 = threading.Thread(target=worker)
            t2.start()
            t2.join()
            self.assertEqual(thread_acquired, [True])

        finally:
            ctx.close()

    def test_prevent_closing_handle_from_another_thread_during_guard(self):
        """
        Verify that if another thread calls close() while a worker is inside guard(),
        close() is blocked until guard() finishes, preventing race conditions.
        """
        lock_path = os.path.join(self.test_dir, "locks", "tx_test_tx_004.lock")
        tx_id = "test_tx_004"
        token = generate_lease_token()
        ctx = acquire_lock_file(lock_path, tx_id, token)

        close_completed_time = []
        guard_in_progress = threading.Event()

        def background_closer():
            guard_in_progress.wait(timeout=2.0)
            ctx.close()
            close_completed_time.append(time.time())

        t = threading.Thread(target=background_closer)
        t.start()

        with ctx.guard():
            guard_in_progress.set()
            time.sleep(0.15)  # Simulate file I/O work
            guard_exit_time = time.time()

        t.join(timeout=2.0)

        self.assertTrue(len(close_completed_time) == 1)
        # close() must have finished strictly at or after guard_exit_time
        self.assertGreaterEqual(close_completed_time[0], guard_exit_time - 0.01)
        self.assertFalse(ctx.is_active)

    def test_inactive_and_expired_states_raise(self):
        """Verify that inactive context or expired lease raises appropriate exceptions."""
        lock_path = os.path.join(self.test_dir, "locks", "tx_test_tx_005.lock")
        tx_id = "test_tx_005"
        token = generate_lease_token()
        ctx = acquire_lock_file(lock_path, tx_id, token)

        # Close makes it inactive
        ctx.close()
        with self.assertRaises(LockLostException):
            with ctx.guard():
                pass

        # Expired lease
        ctx2 = LockContext(
            tx_id="tx_expired",
            lease_token="EXPIRED",
            lock_handle=123,
            expected_lock_file_id=1,
            expected_lock_volume_serial=1
        )
        with self.assertRaises(LeaseExpiredError):
            with ctx2.guard():
                pass

    def test_invalid_handle_values_raise_lock_lost(self):
        """Verify that null or invalid handle values are safely caught by is_invalid_handle and guard()."""
        from core.security import is_invalid_handle
        self.assertTrue(is_invalid_handle(0))
        self.assertTrue(is_invalid_handle(None))
        self.assertTrue(is_invalid_handle(-1))
        self.assertTrue(is_invalid_handle(0xFFFFFFFFFFFFFFFF))
        self.assertTrue(is_invalid_handle(18446744073709551615))
        self.assertFalse(is_invalid_handle(100))

        # Test guard with invalid handles
        for bad_h in [0, -1, 0xFFFFFFFFFFFFFFFF]:
            ctx = LockContext(
                tx_id="tx_bad_handle",
                lease_token=generate_lease_token(),
                lock_handle=bad_h,
                expected_lock_file_id=1,
                expected_lock_volume_serial=1
            )
            with self.subTest(bad_h=bad_h):
                with self.assertRaises(LockLostException):
                    with ctx.guard():
                        pass


if __name__ == "__main__":
    unittest.main()
