"""
Unit tests for resumable streaming FileDownloader and DownloadManager.
Conforms strictly to PROJECT_SPEC.md v1.1.5 Section 3, 4, 11 and Stage 4 requirements.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core._network_compat import requests
from core.downloader import (
    DEFAULT_CHUNK_SIZE,
    DownloadManager,
    DownloadProgress,
    DownloadStatus,
    DownloadTask,
    FileDownloader,
    verify_file_hashes,
)
from core.exceptions import (
    DownloadCancelledError,
    DownloadError,
    DownloadRangeError,
    DownloadSizeMismatchError,
    HashMismatchError,
    NetworkError,
)
from tests.conftest import IsolatedEnvironmentTestCase


class MockStreamResponse:
    """Mock HTTP streaming response providing deterministic chunk iteration and interruption."""

    def __init__(
        self,
        status_code: int,
        chunks: list[bytes],
        headers: dict | None = None,
        interrupt_after_bytes: int = -1,
    ):
        self.status_code = status_code
        self.chunks = chunks
        self.headers = headers or {}
        self.interrupt_after_bytes = interrupt_after_bytes
        self.url = "https://mock.example/download/archive.zip"
        self.closed = False

    def iter_content(self, chunk_size: int = DEFAULT_CHUNK_SIZE):
        bytes_sent = 0
        for chunk in self.chunks:
            if self.interrupt_after_bytes >= 0 and bytes_sent >= self.interrupt_after_bytes:
                raise requests.ConnectionError("Simulated socket connection drop midway through download")
            bytes_sent += len(chunk)
            yield chunk

    def close(self):
        self.closed = True


class TestFileDownloader(IsolatedEnvironmentTestCase):

    def setUp(self):
        super().setUp()
        self.dest_dir = Path(self.test_dir) / "downloads"
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        self.filename = "EP01.zip"
        self.test_payload = b"SIMS4_DLC_EP01_FULL_CONTENT_PAYLOAD_BYTES_" * 50  # 2150 bytes
        self.expected_size = len(self.test_payload)
        self.expected_sha256 = hashlib.sha256(self.test_payload).hexdigest()
        self.expected_md5 = hashlib.md5(self.test_payload).hexdigest()

    def test_scenario_1_new_file_download_from_scratch(self):
        """Scenario 1: Fresh download with 200 OK, full stream, and SHA-256 verification."""
        mock_session = MagicMock(spec=requests.Session)
        mock_response = MockStreamResponse(
            status_code=200,
            chunks=[self.test_payload[:1000], self.test_payload[1000:]],
            headers={"Content-Length": str(self.expected_size)},
        )
        mock_session.request.return_value = mock_response

        downloader = FileDownloader(session=mock_session)
        task = DownloadTask(
            url="https://mock.example/EP01.zip",
            destination_dir=self.dest_dir,
            filename=self.filename,
            expected_size=self.expected_size,
            expected_sha256=self.expected_sha256,
        )

        progress = downloader.download(task)

        self.assertEqual(progress.status, DownloadStatus.COMPLETED)
        self.assertTrue(task.final_path.is_file())
        self.assertFalse(task.part_path.exists())
        self.assertEqual(task.final_path.stat().st_size, self.expected_size)
        with open(task.final_path, "rb") as f:
            self.assertEqual(f.read(), self.test_payload)

    def test_scenario_2_and_4_resume_existing_part_with_range_206(self):
        """
        Scenario 2 & 4: Existing .part file sends Range: bytes=X-, receives 206 Partial Content,
        validates Content-Range, appends remainder, and finalizes.
        """
        part_bytes = self.test_payload[:800]
        remaining_bytes = self.test_payload[800:]

        # Pre-create .part file
        task = DownloadTask(
            url="https://mock.example/EP01.zip",
            destination_dir=self.dest_dir,
            filename=self.filename,
            expected_size=self.expected_size,
            expected_sha256=self.expected_sha256,
        )
        with open(task.part_path, "wb") as f:
            f.write(part_bytes)

        mock_session = MagicMock(spec=requests.Session)
        mock_response = MockStreamResponse(
            status_code=206,
            chunks=[remaining_bytes],
            headers={
                "Content-Range": f"bytes 800-{self.expected_size - 1}/{self.expected_size}",
                "Content-Length": str(len(remaining_bytes)),
            },
        )
        mock_session.request.return_value = mock_response

        downloader = FileDownloader(session=mock_session)
        progress = downloader.download(task)

        self.assertEqual(progress.status, DownloadStatus.COMPLETED)
        # Verify Range header was sent
        _, kwargs = mock_session.request.call_args
        self.assertEqual(kwargs["headers"].get("Range"), "bytes=800-")
        self.assertTrue(task.final_path.is_file())
        self.assertFalse(task.part_path.exists())
        with open(task.final_path, "rb") as f:
            self.assertEqual(f.read(), self.test_payload)

    def test_scenario_3_part_file_already_equals_expected_size(self):
        """
        Scenario 3: Existing .part file already has full expected size.
        Verifies hash, performs atomic rename without network request.
        """
        task = DownloadTask(
            url="https://mock.example/EP01.zip",
            destination_dir=self.dest_dir,
            filename=self.filename,
            expected_size=self.expected_size,
            expected_sha256=self.expected_sha256,
        )
        with open(task.part_path, "wb") as f:
            f.write(self.test_payload)

        mock_session = MagicMock(spec=requests.Session)
        downloader = FileDownloader(session=mock_session)

        progress = downloader.download(task)

        self.assertEqual(progress.status, DownloadStatus.COMPLETED)
        self.assertTrue(task.final_path.is_file())
        self.assertFalse(task.part_path.exists())
        # No HTTP request should have been performed
        mock_session.request.assert_not_called()

    def test_scenario_5_server_ignores_range_and_returns_200_ok(self):
        """
        Scenario 5: Server ignores Range header and returns 200 OK with full file.
        Downloader must truncate .part, stream from byte 0, and not corrupt file.
        """
        task = DownloadTask(
            url="https://mock.example/EP01.zip",
            destination_dir=self.dest_dir,
            filename=self.filename,
            expected_size=self.expected_size,
            expected_sha256=self.expected_sha256,
        )
        # Existing .part with old stale data
        with open(task.part_path, "wb") as f:
            f.write(b"STALE_OLD_PART_DATA_500_BYTES")

        mock_session = MagicMock(spec=requests.Session)
        mock_response = MockStreamResponse(
            status_code=200,
            chunks=[self.test_payload],
            headers={"Content-Length": str(self.expected_size)},
        )
        mock_session.request.return_value = mock_response

        downloader = FileDownloader(session=mock_session)
        progress = downloader.download(task)

        self.assertEqual(progress.status, DownloadStatus.COMPLETED)
        self.assertTrue(task.final_path.is_file())
        with open(task.final_path, "rb") as f:
            self.assertEqual(f.read(), self.test_payload)

    def test_scenario_6_server_returns_malformed_or_mismatched_content_range(self):
        """
        Scenario 6: Server returns Content-Range starting at wrong offset.
        Raises DownloadRangeError.
        """
        task = DownloadTask(
            url="https://mock.example/EP01.zip",
            destination_dir=self.dest_dir,
            filename=self.filename,
            expected_size=self.expected_size,
            max_retries=1,
        )
        with open(task.part_path, "wb") as f:
            f.write(self.test_payload[:500])

        mock_session = MagicMock(spec=requests.Session)
        # Server returns start 0 instead of requested 500
        mock_response = MockStreamResponse(
            status_code=206,
            chunks=[self.test_payload],
            headers={"Content-Range": f"bytes 0-{self.expected_size - 1}/{self.expected_size}"},
        )
        mock_session.request.return_value = mock_response

        downloader = FileDownloader(session=mock_session)
        with self.assertRaises(DownloadError):
            downloader.download(task)

    def test_scenario_7_and_12_interrupted_connection_and_automatic_resumption(self):
        """
        Scenario 7 & 12: Connection drops midway. First attempt writes 600 bytes,
        then fails. Retry reconnects with Range: bytes=600- and finishes remaining bytes.
        """
        task = DownloadTask(
            url="https://mock.example/EP01.zip",
            destination_dir=self.dest_dir,
            filename=self.filename,
            expected_size=self.expected_size,
            expected_sha256=self.expected_sha256,
            max_retries=3,
            backoff_factor=0.01,
        )

        chunk1 = self.test_payload[:600]
        chunk2 = self.test_payload[600:1200]
        chunk3 = self.test_payload[1200:]

        resp_failed = MockStreamResponse(
            status_code=200,
            chunks=[chunk1, chunk2],
            headers={"Content-Length": str(self.expected_size)},
            interrupt_after_bytes=600,
        )
        resp_resume = MockStreamResponse(
            status_code=206,
            chunks=[self.test_payload[600:]],
            headers={"Content-Range": f"bytes 600-{self.expected_size - 1}/{self.expected_size}"},
        )

        mock_session = MagicMock(spec=requests.Session)
        mock_session.request.side_effect = [resp_failed, resp_resume]

        downloader = FileDownloader(session=mock_session)
        progress = downloader.download(task)

        self.assertEqual(progress.status, DownloadStatus.COMPLETED)
        self.assertTrue(task.final_path.is_file())
        with open(task.final_path, "rb") as f:
            self.assertEqual(f.read(), self.test_payload)
        self.assertEqual(mock_session.request.call_count, 2)

    def test_scenario_8_server_temporarily_returns_5xx_and_recovers(self):
        """Scenario 8: Server temporarily returns 503 Service Unavailable, then succeeds on retry."""
        task = DownloadTask(
            url="https://mock.example/EP01.zip",
            destination_dir=self.dest_dir,
            filename=self.filename,
            expected_size=self.expected_size,
            expected_sha256=self.expected_sha256,
            max_retries=3,
            backoff_factor=0.01,
        )

        resp_503 = MockStreamResponse(status_code=503, chunks=[])
        resp_200 = MockStreamResponse(
            status_code=200,
            chunks=[self.test_payload],
            headers={"Content-Length": str(self.expected_size)},
        )

        mock_session = MagicMock(spec=requests.Session)
        mock_session.request.side_effect = [resp_503, resp_200]

        downloader = FileDownloader(session=mock_session)
        progress = downloader.download(task)

        self.assertEqual(progress.status, DownloadStatus.COMPLETED)
        self.assertTrue(task.final_path.is_file())

    def test_scenario_9_final_size_mismatch_deletes_corrupted_part(self):
        """
        Scenario 9: Final downloaded size does not match expected_size.
        Corrupted .part file must be removed and DownloadSizeMismatchError raised.
        """
        task = DownloadTask(
            url="https://mock.example/EP01.zip",
            destination_dir=self.dest_dir,
            filename=self.filename,
            expected_size=self.expected_size + 100,  # Expect 100 more bytes than delivered
            max_retries=1,
        )

        mock_session = MagicMock(spec=requests.Session)
        mock_session.request.return_value = MockStreamResponse(
            status_code=200,
            chunks=[self.test_payload],
            headers={"Content-Length": str(self.expected_size)},
        )

        downloader = FileDownloader(session=mock_session)
        with self.assertRaises(DownloadSizeMismatchError):
            downloader.download(task)

        # Ensure corrupted .part file was deleted
        self.assertFalse(task.part_path.exists())
        self.assertFalse(task.final_path.exists())

    def test_scenario_10_hash_mismatch_deletes_corrupted_part(self):
        """
        Scenario 10: Checksum does not match expected_sha256.
        Corrupted .part file must be removed and HashMismatchError raised.
        """
        task = DownloadTask(
            url="https://mock.example/EP01.zip",
            destination_dir=self.dest_dir,
            filename=self.filename,
            expected_size=self.expected_size,
            expected_sha256="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
            max_retries=1,
        )

        mock_session = MagicMock(spec=requests.Session)
        mock_session.request.return_value = MockStreamResponse(
            status_code=200,
            chunks=[self.test_payload],
            headers={"Content-Length": str(self.expected_size)},
        )

        downloader = FileDownloader(session=mock_session)
        with self.assertRaises(HashMismatchError):
            downloader.download(task)

        self.assertFalse(task.part_path.exists())
        self.assertFalse(task.final_path.exists())

    def test_scenario_11_cancellation_during_download_preserves_downloaded_part(self):
        """
        Scenario 11: Cancellation midway through download.
        Raises DownloadCancelledError, but preserves .part bytes on disk for resumption.
        """
        cancel_event = threading.Event()
        task = DownloadTask(
            url="https://mock.example/EP01.zip",
            destination_dir=self.dest_dir,
            filename=self.filename,
            expected_size=self.expected_size,
        )

        class CancellableStreamResponse(MockStreamResponse):
            def iter_content(self, chunk_size: int = DEFAULT_CHUNK_SIZE):
                for i, chunk in enumerate(self.chunks):
                    if i == 1:
                        cancel_event.set()
                    yield chunk

        mock_session = MagicMock(spec=requests.Session)
        chunk1 = self.test_payload[:500]
        chunk2 = self.test_payload[500:1000]
        chunk3 = self.test_payload[1000:]
        mock_session.request.return_value = CancellableStreamResponse(
            status_code=200,
            chunks=[chunk1, chunk2, chunk3],
            headers={"Content-Length": str(self.expected_size)},
        )

        downloader = FileDownloader(session=mock_session)
        with self.assertRaises(DownloadCancelledError):
            downloader.download(task, cancel_event=cancel_event)

        # .part file must still exist with partial content preserved
        self.assertTrue(task.part_path.is_file())
        self.assertGreaterEqual(task.part_path.stat().st_size, 500)
        self.assertFalse(task.final_path.exists())

    def test_already_existing_verified_final_file_skips_download(self):
        """Existing final file matching size and hash immediately reports COMPLETED without re-download."""
        task = DownloadTask(
            url="https://mock.example/EP01.zip",
            destination_dir=self.dest_dir,
            filename=self.filename,
            expected_size=self.expected_size,
            expected_sha256=self.expected_sha256,
        )
        with open(task.final_path, "wb") as f:
            f.write(self.test_payload)

        mock_session = MagicMock(spec=requests.Session)
        downloader = FileDownloader(session=mock_session)

        progress = downloader.download(task)

        self.assertEqual(progress.status, DownloadStatus.COMPLETED)
        mock_session.request.assert_not_called()

    def test_verify_file_hashes_helper(self):
        """Test verify_file_hashes with valid and invalid MD5 and SHA-256."""
        test_file = self.dest_dir / "hash_test.bin"
        with open(test_file, "wb") as f:
            f.write(self.test_payload)

        # Valid hashes pass
        self.assertTrue(verify_file_hashes(test_file, expected_sha256=self.expected_sha256))
        self.assertTrue(verify_file_hashes(test_file, expected_md5=self.expected_md5))

        # Invalid hashes raise HashMismatchError
        with self.assertRaises(HashMismatchError):
            verify_file_hashes(test_file, expected_sha256="bad_sha256")
        with self.assertRaises(HashMismatchError):
            verify_file_hashes(test_file, expected_md5="bad_md5")


class TestDownloadManager(IsolatedEnvironmentTestCase):

    def setUp(self):
        super().setUp()
        self.dest_dir = Path(self.test_dir) / "downloads_mgr"
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        self.payload = b"DOWNLOAD_MANAGER_PAYLOAD_" * 40
        self.sha256 = hashlib.sha256(self.payload).hexdigest()

    def test_manager_queue_and_concurrent_execution(self):
        """Test submitting tasks to DownloadManager with asynchronous completion."""
        mock_session = MagicMock(spec=requests.Session)
        mock_session.request.return_value = MockStreamResponse(
            status_code=200,
            chunks=[self.payload],
            headers={"Content-Length": str(len(self.payload))},
        )
        downloader = FileDownloader(session=mock_session)
        manager = DownloadManager(max_concurrent=2, downloader=downloader)

        task1 = DownloadTask(
            url="https://mock.example/file1.zip",
            destination_dir=self.dest_dir,
            filename="file1.zip",
            expected_size=len(self.payload),
            expected_sha256=self.sha256,
        )
        task2 = DownloadTask(
            url="https://mock.example/file2.zip",
            destination_dir=self.dest_dir,
            filename="file2.zip",
            expected_size=len(self.payload),
            expected_sha256=self.sha256,
        )

        id1 = manager.submit(task1)
        id2 = manager.submit(task2)

        # Wait up to 3 seconds for background threads
        for _ in range(30):
            p1 = manager.get_progress(id1)
            p2 = manager.get_progress(id2)
            if p1 and p2 and p1.status == DownloadStatus.COMPLETED and p2.status == DownloadStatus.COMPLETED:
                break
            time.sleep(0.1)

        self.assertEqual(manager.get_progress(id1).status, DownloadStatus.COMPLETED)
        self.assertEqual(manager.get_progress(id2).status, DownloadStatus.COMPLETED)
        self.assertTrue(task1.final_path.is_file())
        self.assertTrue(task2.final_path.is_file())

    def test_manager_cancel_task(self):
        """Test cancelling a task through DownloadManager."""
        class SlowStreamResponse(MockStreamResponse):
            def iter_content(self, chunk_size: int = DEFAULT_CHUNK_SIZE):
                for chunk in self.chunks:
                    time.sleep(0.03)
                    yield chunk

        mock_session = MagicMock(spec=requests.Session)
        mock_session.request.return_value = SlowStreamResponse(
            status_code=200,
            chunks=[b"chunk"] * 50,
            headers={"Content-Length": "250"},
        )
        downloader = FileDownloader(session=mock_session)
        manager = DownloadManager(max_concurrent=1, downloader=downloader)

        task = DownloadTask(
            url="https://mock.example/cancel.zip",
            destination_dir=self.dest_dir,
            filename="cancel.zip",
        )

        task_id = manager.submit(task)
        time.sleep(0.05)
        cancelled = manager.cancel_task(task_id)
        self.assertTrue(cancelled)

        for _ in range(30):
            p = manager.get_progress(task_id)
            if p and p.status == DownloadStatus.CANCELLED:
                break
            time.sleep(0.05)

        p = manager.get_progress(task_id)
        self.assertEqual(p.status, DownloadStatus.CANCELLED)


if __name__ == "__main__":
    unittest.main()
