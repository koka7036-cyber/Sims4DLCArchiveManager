"""
Resumable streaming downloader with HTTP Range (206 Partial Content), .part buffering,
and cryptographic integrity verification.
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 3, 4, 11 and Stage 4 requirements.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core._network_compat import requests, HTTPAdapter, Retry
from core.exceptions import (
    DownloadCancelledError,
    DownloadError,
    DownloadRangeError,
    DownloadSizeMismatchError,
    HashMismatchError,
    NetworkError,
)
from core.logger import get_logger

logger = get_logger("core.downloader")

DEFAULT_CHUNK_SIZE = 64 * 1024  # 64 KB chunks
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_FACTOR = 1.0

CONTENT_RANGE_PATTERN = re.compile(
    r"^bytes\s+(\d+)-(\d+)/(?:\*|(\d+))",
    re.IGNORECASE,
)


class DownloadStatus(str, Enum):
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class DownloadProgress:
    """Real-time progress metrics for active download."""
    task_id: str
    status: DownloadStatus
    downloaded_bytes: int = 0
    total_bytes: Optional[int] = None
    speed_bps: float = 0.0
    eta_seconds: Optional[float] = None
    progress_percent: float = 0.0
    part_path: Optional[Path] = None
    final_path: Optional[Path] = None
    error_message: Optional[str] = None


@dataclass
class DownloadTask:
    """Descriptor for a download operation."""
    url: str
    destination_dir: Path
    filename: str
    task_id: Optional[str] = None
    expected_size: Optional[int] = None
    expected_sha256: Optional[str] = None
    expected_md5: Optional[str] = None
    chunk_size: int = DEFAULT_CHUNK_SIZE
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR

    def __post_init__(self):
        if not self.task_id:
            self.task_id = self.filename
        self.destination_dir = Path(self.destination_dir)

    @property
    def final_path(self) -> Path:
        return self.destination_dir / self.filename

    @property
    def part_path(self) -> Path:
        return self.destination_dir / f"{self.filename}.part"


def verify_file_hashes(
    file_path: Path,
    expected_sha256: Optional[str] = None,
    expected_md5: Optional[str] = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> bool:
    """
    Computes cryptographic checksums of the file and verifies against expected values.
    Raises HashMismatchError if any provided hash does not match.
    """
    if not expected_sha256 and not expected_md5:
        return True

    sha256_hasher = hashlib.sha256() if expected_sha256 else None
    md5_hasher = hashlib.md5() if expected_md5 else None

    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            if sha256_hasher:
                sha256_hasher.update(chunk)
            if md5_hasher:
                md5_hasher.update(chunk)

    if sha256_hasher and expected_sha256:
        actual_sha256 = sha256_hasher.hexdigest().lower()
        if actual_sha256 != expected_sha256.lower():
            raise HashMismatchError(
                f"SHA-256 mismatch for {file_path.name}: expected {expected_sha256.lower()}, got {actual_sha256}"
            )

    if md5_hasher and expected_md5:
        actual_md5 = md5_hasher.hexdigest().lower()
        if actual_md5 != expected_md5.lower():
            raise HashMismatchError(
                f"MD5 mismatch for {file_path.name}: expected {expected_md5.lower()}, got {actual_md5}"
            )

    return True


class FileDownloader:
    """
    Resumable streaming HTTP downloader with HTTP Range, .part buffering,
    and automatic retries.
    """

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: tuple[float, float] = (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT),
    ) -> None:
        self.timeout = timeout
        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            retry_strategy = Retry(
                total=DEFAULT_MAX_RETRIES,
                connect=DEFAULT_MAX_RETRIES,
                read=DEFAULT_MAX_RETRIES,
                status=DEFAULT_MAX_RETRIES,
                backoff_factor=DEFAULT_BACKOFF_FACTOR,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET", "HEAD"],
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)

    def download(
        self,
        task: DownloadTask,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> DownloadProgress:
        """
        Executes download of task with full resumption and verification.
        """
        task.destination_dir.mkdir(parents=True, exist_ok=True)
        final_path = task.final_path
        part_path = task.part_path

        # 1. Check if final file already exists and is complete
        if final_path.is_file():
            existing_size = final_path.stat().st_size
            size_matches = task.expected_size is None or existing_size == task.expected_size

            if size_matches:
                try:
                    verify_file_hashes(final_path, task.expected_sha256, task.expected_md5)
                    logger.info("File %s already exists and passed verification.", final_path.name)
                    progress = DownloadProgress(
                        task_id=task.task_id or task.filename,
                        status=DownloadStatus.COMPLETED,
                        downloaded_bytes=existing_size,
                        total_bytes=existing_size,
                        speed_bps=0.0,
                        eta_seconds=0.0,
                        progress_percent=100.0,
                        part_path=part_path,
                        final_path=final_path,
                    )
                    if progress_callback:
                        progress_callback(progress)
                    return progress
                except HashMismatchError:
                    logger.warning("Existing file %s has invalid hash, redownloading.", final_path.name)
                    final_path.unlink()
            else:
                logger.warning("Existing file %s size (%d) mismatch, redownloading.", final_path.name, existing_size)
                final_path.unlink()

        # 2. Check partial file (.part)
        initial_part_size = 0
        if part_path.is_file():
            initial_part_size = part_path.stat().st_size
            if task.expected_size is not None:
                if initial_part_size == task.expected_size:
                    # Part file matches expected size: check hash
                    try:
                        verify_file_hashes(part_path, task.expected_sha256, task.expected_md5)
                        os.replace(str(part_path), str(final_path))
                        progress = DownloadProgress(
                            task_id=task.task_id or task.filename,
                            status=DownloadStatus.COMPLETED,
                            downloaded_bytes=initial_part_size,
                            total_bytes=initial_part_size,
                            speed_bps=0.0,
                            eta_seconds=0.0,
                            progress_percent=100.0,
                            part_path=part_path,
                            final_path=final_path,
                        )
                        if progress_callback:
                            progress_callback(progress)
                        return progress
                    except HashMismatchError:
                        logger.warning("Existing .part file %s has invalid hash, deleting.", part_path.name)
                        part_path.unlink()
                        initial_part_size = 0
                elif initial_part_size > task.expected_size:
                    logger.warning("Existing .part file %s exceeds expected size, deleting.", part_path.name)
                    part_path.unlink()
                    initial_part_size = 0

        # 3. Main download loop with network retry and resume support
        attempt = 0
        current_downloaded = initial_part_size
        total_size = task.expected_size

        while True:
            if cancel_event and cancel_event.is_set():
                progress = DownloadProgress(
                    task_id=task.task_id or task.filename,
                    status=DownloadStatus.CANCELLED,
                    downloaded_bytes=current_downloaded,
                    total_bytes=total_size,
                    part_path=part_path,
                    final_path=final_path,
                )
                if progress_callback:
                    progress_callback(progress)
                raise DownloadCancelledError(f"Download of {task.filename} was cancelled")

            attempt += 1
            try:
                current_downloaded, total_size = self._download_chunked(
                    task=task,
                    downloaded_so_far=current_downloaded,
                    total_expected=total_size,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                )
                break  # Successful download
            except DownloadCancelledError:
                raise
            except (requests.RequestException, NetworkError, OSError) as e:
                logger.warning(
                    "Network/IO error during download attempt %d/%d for %s: %s",
                    attempt,
                    task.max_retries,
                    task.filename,
                    str(e),
                )
                if attempt >= task.max_retries:
                    logger.error("Download failed after %d attempts for %s", attempt, task.filename)
                    progress = DownloadProgress(
                        task_id=task.task_id or task.filename,
                        status=DownloadStatus.FAILED,
                        downloaded_bytes=current_downloaded,
                        total_bytes=total_size,
                        part_path=part_path,
                        final_path=final_path,
                        error_message=str(e),
                    )
                    if progress_callback:
                        progress_callback(progress)
                    raise DownloadError(f"Download failed after {attempt} attempts: {e}") from e

                # Backoff before reconnecting and re-stating current .part size
                backoff_time = task.backoff_factor * (2 ** (attempt - 1))
                time.sleep(min(backoff_time, 15.0))

                if part_path.is_file():
                    current_downloaded = part_path.stat().st_size
                else:
                    current_downloaded = 0

        # 4. Verification and atomic finalization
        if not part_path.is_file():
            raise DownloadError(f"Expected temporary file {part_path} does not exist after download")

        final_actual_size = part_path.stat().st_size
        if task.expected_size is not None and final_actual_size != task.expected_size:
            logger.error(
                "Final size mismatch for %s: expected %d, got %d. Deleting corrupted .part file.",
                task.filename,
                task.expected_size,
                final_actual_size,
            )
            part_path.unlink(missing_ok=True)
            raise DownloadSizeMismatchError(
                f"File size mismatch for {task.filename}: expected {task.expected_size}, got {final_actual_size}"
            )

        # Verify hashes
        try:
            verify_file_hashes(
                file_path=part_path,
                expected_sha256=task.expected_sha256,
                expected_md5=task.expected_md5,
                chunk_size=task.chunk_size,
            )
        except HashMismatchError as e:
            logger.error("Hash verification failed for %s. Deleting corrupted .part file.", task.filename)
            part_path.unlink(missing_ok=True)
            raise

        # Atomic rename to target file
        os.replace(str(part_path), str(final_path))
        logger.info("Successfully downloaded and verified %s -> %s", task.filename, final_path)

        completed_progress = DownloadProgress(
            task_id=task.task_id or task.filename,
            status=DownloadStatus.COMPLETED,
            downloaded_bytes=final_actual_size,
            total_bytes=final_actual_size,
            speed_bps=0.0,
            eta_seconds=0.0,
            progress_percent=100.0,
            part_path=part_path,
            final_path=final_path,
        )
        if progress_callback:
            progress_callback(completed_progress)

        return completed_progress

    def _download_chunked(
        self,
        task: DownloadTask,
        downloaded_so_far: int,
        total_expected: Optional[int],
        progress_callback: Optional[Callable[[DownloadProgress], None]],
        cancel_event: Optional[threading.Event],
    ) -> tuple[int, Optional[int]]:
        """
        Connects and streams data into the .part file, handling Range headers.
        Returns (current_downloaded_bytes, total_bytes).
        """
        headers: Dict[str, str] = {}
        is_range_request = downloaded_so_far > 0
        if is_range_request:
            headers["Range"] = f"bytes={downloaded_so_far}-"

        part_path = task.part_path

        response = self.session.request(
            method="GET",
            url=task.url,
            headers=headers,
            stream=True,
            timeout=self.timeout,
        )

        status_code = response.status_code

        # Handle rate limiting / server errors
        if status_code in (429, 500, 502, 503, 504):
            response.close()
            raise NetworkError(f"Server returned transient status code {status_code}")

        # Handle Range Not Satisfiable (416)
        if status_code == 416:
            response.close()
            # If server says range is invalid, and we had existing bytes, wipe and restart
            logger.warning("Server returned 416 Range Not Satisfiable for %s. Resetting .part file.", task.filename)
            if part_path.is_file():
                part_path.unlink()
            raise NetworkError("HTTP 416 Range Not Satisfiable - resetting download from 0")

        if status_code not in (200, 206):
            response.close()
            raise DownloadError(f"Unexpected HTTP status {status_code} during download of {task.filename}")

        # Evaluate Range response vs 200 OK
        file_mode = "ab"
        current_downloaded = downloaded_so_far
        total_bytes = total_expected

        if is_range_request:
            if status_code == 206:
                # Server respected Range header
                content_range = response.headers.get("Content-Range")
                if content_range:
                    match = CONTENT_RANGE_PATTERN.match(content_range.strip())
                    if not match:
                        response.close()
                        raise DownloadRangeError(f"Malformed Content-Range: {content_range}")
                    range_start = int(match.group(1))
                    if range_start != downloaded_so_far:
                        response.close()
                        raise DownloadRangeError(
                            f"Content-Range start mismatch: requested {downloaded_so_far}, got {range_start}"
                        )
                    if match.group(3):
                        total_bytes = int(match.group(3))
                file_mode = "ab"
            elif status_code == 200:
                # Mandated by PROJECT_SPEC.md: Server ignored Range header and sent entire file from 0
                logger.warning(
                    "Server ignored Range header and responded with 200 OK for %s. Restarting stream from 0.",
                    task.filename,
                )
                file_mode = "wb"
                current_downloaded = 0
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        total_bytes = int(content_length)
                    except ValueError:
                        pass
        else:
            # Clean download from 0
            file_mode = "wb"
            current_downloaded = 0
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    total_bytes = int(content_length)
                except ValueError:
                    pass

        # Metrics tracking
        last_time = time.time()
        bytes_since_last = 0
        speed_bps = 0.0
        first_chunk = True

        try:
            with open(part_path, file_mode) as f:
                for chunk in response.iter_content(chunk_size=task.chunk_size):
                    if cancel_event and cancel_event.is_set():
                        f.flush()
                        os.fsync(f.fileno())
                        response.close()
                        raise DownloadCancelledError(f"Download of {task.filename} cancelled by user")

                    if chunk:
                        f.write(chunk)
                        chunk_len = len(chunk)
                        current_downloaded += chunk_len
                        bytes_since_last += chunk_len

                        now = time.time()
                        elapsed = now - last_time

                        # Emit progress on first chunk or when ~0.5s has elapsed
                        if first_chunk or elapsed >= 0.5:
                            if elapsed > 0 and bytes_since_last > 0:
                                instant_speed = bytes_since_last / elapsed
                                if speed_bps <= 0.0 or first_chunk:
                                    speed_bps = instant_speed
                                else:
                                    # Exponential moving average (EMA) for stable, non-jittery speed display
                                    speed_bps = 0.7 * instant_speed + 0.3 * speed_bps

                            last_time = now
                            bytes_since_last = 0
                            first_chunk = False

                            eta_seconds = None
                            progress_percent = 0.0
                            if total_bytes and total_bytes > 0:
                                progress_percent = min(100.0, (current_downloaded / total_bytes) * 100.0)
                                if speed_bps > 0:
                                    remaining_bytes = max(0, total_bytes - current_downloaded)
                                    eta_seconds = remaining_bytes / speed_bps

                            if progress_callback:
                                progress_callback(DownloadProgress(
                                    task_id=task.task_id or task.filename,
                                    status=DownloadStatus.DOWNLOADING,
                                    downloaded_bytes=current_downloaded,
                                    total_bytes=total_bytes,
                                    speed_bps=speed_bps,
                                    eta_seconds=eta_seconds,
                                    progress_percent=progress_percent,
                                    part_path=part_path,
                                    final_path=task.final_path,
                                ))

                f.flush()
                os.fsync(f.fileno())

                # Final in-flight progress notification after all chunks stream successfully
                if progress_callback:
                    final_pct = 100.0 if (total_bytes and current_downloaded >= total_bytes) else (
                        (current_downloaded / total_bytes * 100.0) if total_bytes and total_bytes > 0 else 0.0
                    )
                    progress_callback(DownloadProgress(
                        task_id=task.task_id or task.filename,
                        status=DownloadStatus.DOWNLOADING,
                        downloaded_bytes=current_downloaded,
                        total_bytes=total_bytes,
                        speed_bps=speed_bps,
                        eta_seconds=0.0 if final_pct >= 100.0 else None,
                        progress_percent=final_pct,
                        part_path=part_path,
                        final_path=task.final_path,
                    ))
        finally:
            response.close()

        return current_downloaded, total_bytes


class DownloadManager:
    """
    Manager for queuing and concurrently running multiple file downloads
    with thread-safe tracking and cancellation.
    """

    def __init__(
        self,
        max_concurrent: int = 2,
        downloader: Optional[FileDownloader] = None,
    ) -> None:
        self.max_concurrent = max_concurrent
        self.downloader = downloader or FileDownloader()
        self._semaphore = threading.Semaphore(max_concurrent)
        self._lock = threading.Lock()
        self._cancel_events: Dict[str, threading.Event] = {}
        self._tasks: Dict[str, DownloadTask] = {}
        self._progress: Dict[str, DownloadProgress] = {}
        self._active_threads: Dict[str, threading.Thread] = {}

    def get_progress(self, task_id: str) -> Optional[DownloadProgress]:
        with self._lock:
            return self._progress.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        """Signal cancellation for the specified task."""
        with self._lock:
            event = self._cancel_events.get(task_id)
            if event:
                event.set()
                return True
            return False

    def submit(
        self,
        task: DownloadTask,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
    ) -> str:
        """Submits a download task for asynchronous background processing."""
        task_id = task.task_id or task.filename
        cancel_event = threading.Event()

        with self._lock:
            self._cancel_events[task_id] = cancel_event
            self._tasks[task_id] = task
            self._progress[task_id] = DownloadProgress(
                task_id=task_id,
                status=DownloadStatus.QUEUED,
                total_bytes=task.expected_size,
                part_path=task.part_path,
                final_path=task.final_path,
            )

        def worker():
            with self._semaphore:
                if cancel_event.is_set():
                    with self._lock:
                        self._progress[task_id].status = DownloadStatus.CANCELLED
                    return

                def on_progress(p: DownloadProgress):
                    with self._lock:
                        self._progress[task_id] = p
                    if progress_callback:
                        progress_callback(p)

                try:
                    self.downloader.download(
                        task=task,
                        progress_callback=on_progress,
                        cancel_event=cancel_event,
                    )
                except DownloadCancelledError:
                    with self._lock:
                        self._progress[task_id].status = DownloadStatus.CANCELLED
                except Exception as e:
                    with self._lock:
                        self._progress[task_id].status = DownloadStatus.FAILED
                        self._progress[task_id].error_message = str(e)
                finally:
                    with self._lock:
                        self._active_threads.pop(task_id, None)

        thread = threading.Thread(target=worker, daemon=True, name=f"Downloader-{task_id}")
        with self._lock:
            self._active_threads[task_id] = thread
        thread.start()

        return task_id
