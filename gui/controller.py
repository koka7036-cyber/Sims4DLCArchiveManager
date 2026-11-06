"""
Application Controller for GUI.
Bridges CustomTkinter views with core and database services.
Strictly conforms to PROJECT_SPEC.md:
- Does not contain business logic of its own.
- Never bypasses LockContext, NTFS checks, or User Consent Gate.
- Handles asynchronous execution via background threads.
- Formats errors into clear, actionable Russian messages for the user.
"""

import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any, Union

from core.logger import get_logger
from core.app_context import AppContext, get_current_context
from core.exceptions import (
    Sims4ManagerError,
    Sims4UserDirectoryNotFoundError,
    FilesystemError,
    UnsupportedFilesystemError,
    NetworkError,
    YandexRateLimitError,
    YandexAuthError,
    YandexResourceNotFoundError,
    DownloadError,
    DownloadCancelledError,
    HashMismatchError,
    CorruptedArchiveError,
    BlockedBinaryError,
    PathTraversalError,
    ArchiveSecurityLimitError,
    ArchiveSymlinkError,
    LockContextError,
    ReparsePointError,
    RecoveryConflictError,
    DatabaseCorruptionError,
)
from branding.branding_manager import BrandingManager
from gui.state import DLCUIItem, DLCStatus, GameUIInfo, OperationState, TaskState

logger = get_logger("app_controller")


class GameNotFoundError(FilesystemError):
    """Raised when The Sims 4 installation cannot be found."""
    pass


class AppController:
    """
    Central controller managing UI state, invoking core services, and dispatching events.
    """

    def __init__(
        self,
        context: Optional[AppContext] = None,
        branding: Optional[BrandingManager] = None
    ):
        self.context = context or get_current_context()
        self.branding = branding or BrandingManager(self.context)

        # State models
        self.game_info = GameUIInfo()
        self.operation_state = OperationState()
        self.dlc_items: Dict[str, DLCUIItem] = {}
        self.active_downloads: Dict[str, DLCUIItem] = {}

        # Observers
        self._listeners: List[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._ui_dispatcher: Optional[Callable[[Callable[[], None]], None]] = None

        # Core service references (instantiated on demand or injected for testing)
        self._game_finder: Optional[Any] = None
        self._catalog_db: Optional[Any] = None
        self._state_db: Optional[Any] = None
        self._yandex_client: Optional[Any] = None
        self._download_manager: Optional[Any] = None
        self._backup_manager: Optional[Any] = None
        self._recovery_engine: Optional[Any] = None

    # --- Listener Management ---

    def set_ui_dispatcher(self, dispatcher: Optional[Callable[[Callable[[], None]], None]]) -> None:
        """Sets dispatcher to invoke UI callbacks on the Tkinter main thread."""
        self._ui_dispatcher = dispatcher

    def add_listener(self, callback: Callable[[], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def notify_change(self) -> None:
        """Notifies all registered UI listeners that state has changed."""
        def _invoke_all():
            for cb in list(self._listeners):
                try:
                    cb()
                except Exception as e:
                    logger.error(f"Error in UI listener callback: {e}")

        # If called from a non-main worker thread and dispatcher is provided, route through Tkinter after()
        if self._ui_dispatcher is not None and threading.current_thread() != threading.main_thread():
            try:
                self._ui_dispatcher(_invoke_all)
            except Exception as e:
                logger.error(f"Failed to dispatch UI update: {e}")
                _invoke_all()
        else:
            _invoke_all()

    # --- UI State Setters ---

    def set_busy(self, task: TaskState, message: str, progress: float = 0.0) -> None:
        with self._lock:
            self.operation_state.is_busy = True
            self.operation_state.task = task
            self.operation_state.status_message = message
            self.operation_state.progress = progress
        self.notify_change()

    def set_idle(self, message: str = "Готов к работе") -> None:
        with self._lock:
            self.operation_state.is_busy = False
            self.operation_state.task = TaskState.IDLE
            self.operation_state.status_message = message
            self.operation_state.progress = 0.0
        self.notify_change()

    def set_error(self, message: str) -> None:
        with self._lock:
            self.operation_state.is_busy = False
            self.operation_state.task = TaskState.IDLE
            self.operation_state.status_message = f"Ошибка: {message}"
            self.operation_state.last_error = message
        self.notify_change()

    # --- Core Service Lazy Getters / Injections ---

    def get_game_finder(self) -> Any:
        if self._game_finder is None:
            from core.game_finder import GameFinder
            self._game_finder = GameFinder()
        return self._game_finder

    def set_game_finder(self, finder: Any) -> None:
        self._game_finder = finder

    def get_catalog_db(self) -> Any:
        if self._catalog_db is None:
            from database.database import CatalogDatabase
            self._catalog_db = CatalogDatabase(self.context.catalog_db_path)
        return self._catalog_db

    def set_catalog_db(self, db: Any) -> None:
        self._catalog_db = db

    def get_state_db(self) -> Any:
        if self._state_db is None:
            from database.database import StateDatabase
            self._state_db = StateDatabase(self.context.state_db_path)
        return self._state_db

    def set_state_db(self, db: Any) -> None:
        self._state_db = db

    def get_yandex_client(self) -> Any:
        if self._yandex_client is None:
            from core.yandex_client import YandexClient
            self._yandex_client = YandexClient()
        return self._yandex_client

    def set_yandex_client(self, client: Any) -> None:
        self._yandex_client = client

    def get_download_manager(self) -> Any:
        if self._download_manager is None:
            from core.downloader import DownloadManager
            self._download_manager = DownloadManager(
                max_concurrent=2
            )
        return self._download_manager

    def set_download_manager(self, manager: Any) -> None:
        self._download_manager = manager

    def get_backup_manager(self) -> Any:
        if self._backup_manager is None:
            from core.backup_manager import BackupManager
            self._backup_manager = BackupManager(app_context=self.context)
        return self._backup_manager

    def set_backup_manager(self, manager: Any) -> None:
        self._backup_manager = manager

    def get_recovery_engine(self) -> Any:
        if self._recovery_engine is None:
            from database.recovery import CrashRecoveryEngine
            self._recovery_engine = CrashRecoveryEngine(
                state_db=self.get_state_db()
            )
        return self._recovery_engine

    def set_recovery_engine(self, engine: Any) -> None:
        self._recovery_engine = engine

    # --- User Actions / Background Tasks ---

    def scan_game_installation(self, custom_path: Optional[str] = None) -> None:
        """
        Scans system for The Sims 4 or validates user-provided path.
        Checks NTFS filesystem and analyzes installed DLCs.
        """
        if self.operation_state.is_busy:
            logger.warning("Cannot start scan_game: another operation is in progress.")
            return

        def _worker():
            self.set_busy(TaskState.SCANNING_GAME, "Поиск The Sims 4 и проверка NTFS...")
            try:
                finder = self.get_game_finder()
                game_inst = None

                if custom_path:
                    if hasattr(finder, "validate_custom_path"):
                        game_inst = finder.validate_custom_path(custom_path)
                    elif hasattr(finder, "validate_game_directory"):
                        p = finder.validate_game_directory(custom_path)
                        from core.game_finder import GameInstallation
                        game_inst = GameInstallation(path=Path(p), volume_path=str(p), filesystem="NTFS", source="manual")
                else:
                    if hasattr(finder, "find_game"):
                        game_inst = finder.find_game()
                    elif hasattr(finder, "find_game_directory"):
                        p = finder.find_game_directory()
                        if p:
                            from core.game_finder import GameInstallation
                            game_inst = GameInstallation(path=Path(p), volume_path=str(p), filesystem="NTFS", source="auto")

                if not game_inst:
                    self.game_info.found = False
                    self.game_info.path = ""
                    self.game_info.error_message = "Игра не обнаружена в реестре или Steam."
                    self.set_idle("The Sims 4 не найдена.")
                    return

                game_path = game_inst.path if hasattr(game_inst, "path") else Path(game_inst)
                fs_name = getattr(game_inst, "filesystem", "NTFS")

                # Verify NTFS
                if fs_name.upper() != "NTFS":
                    raise UnsupportedFilesystemError(
                        f"Каталог игры '{game_path}' расположен на неподдерживаемой файловой системе '{fs_name}' (требуется NTFS)."
                    )

                # Scan installed DLCs
                from core.game_finder import DeepDLCScanner, GameInstallation
                if isinstance(game_inst, GameInstallation):
                    scan_report = DeepDLCScanner.scan_game_installation(game_inst)
                    installed_count = scan_report.installed_count
                    installed_codes = {c.upper() for c, info in scan_report.dlcs.items() if info.is_installed}
                elif hasattr(finder, "deep_scan_dlcs"):
                    installed_list = finder.deep_scan_dlcs(str(game_path))
                    installed_count = len(installed_list)
                    installed_codes = {Path(p).name.upper() for p in installed_list}
                else:
                    temp_inst = GameInstallation(path=game_path, volume_path=str(game_path), filesystem=fs_name, source="manual")
                    scan_report = DeepDLCScanner.scan_game_installation(temp_inst)
                    installed_count = scan_report.installed_count
                    installed_codes = {c.upper() for c, info in scan_report.dlcs.items() if info.is_installed}

                self.game_info.found = True
                self.game_info.path = str(game_path)
                self.game_info.filesystem = fs_name
                self.game_info.installed_dlc_count = installed_count
                self.game_info.error_message = ""

                # Mark corresponding DLC items as INSTALLED
                with self._lock:
                    for code in installed_codes:
                        if code in self.dlc_items:
                            self.dlc_items[code].status = DLCStatus.INSTALLED

                self.set_idle(f"Игра обнаружена: {self.game_info.installed_dlc_count} DLC установлено.")
            except UnsupportedFilesystemError as e:
                self.game_info.found = False
                self.game_info.error_message = str(e)
                self.set_error(str(e))
            except (GameNotFoundError, FilesystemError) as e:
                self.game_info.found = False
                self.game_info.error_message = str(e)
                self.set_idle("The Sims 4 не найдена.")
            except Exception as e:
                logger.error(f"Error during game scan: {e}")
                self.set_error(f"Сбой сканирования игры: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def refresh_catalog(self) -> None:
        """
        Loads or refreshes DLC catalog from Yandex.Disk cloud and updates catalog.db.
        """
        if self.operation_state.is_busy:
            return

        def _worker():
            self.set_busy(TaskState.LOADING_CATALOG, "Загрузка каталога дополнений из облака...")
            try:
                client = self.get_yandex_client()
                catalog_url = self.branding.primary_dlc_folder
                if not catalog_url:
                    raise NetworkError("URL облачного каталога не задан в профиле магазина.")

                resources = client.list_public_folder_items(catalog_url)
                cat_db = self.get_catalog_db()

                with self._lock:
                    for res in resources:
                        if not res.is_archive:
                            continue
                        name = res.name
                        item_id = Path(name).stem.upper()
                        # Guess DLC type
                        dlc_type = "EP"
                        if "GP" in item_id:
                            dlc_type = "GP"
                        elif "SP" in item_id:
                            dlc_type = "SP"
                        elif "FP" in item_id:
                            dlc_type = "FP"

                        # Check if archive is already downloaded
                        archive_path = self.context.downloads_dir / name
                        initial_status = DLCStatus.AVAILABLE
                        if archive_path.is_file():
                            initial_status = DLCStatus.DOWNLOADED

                        item = DLCUIItem(
                            item_id=item_id,
                            title=item_id,
                            dlc_type=dlc_type,
                            archive_name=name,
                            archive_size=res.size,
                            unpacked_size=res.size * 2,  # Approximation until inspected
                            status=initial_status,
                            public_url=res.path or catalog_url,
                            sha256=res.sha256,
                            md5=res.md5
                        )
                        self.dlc_items[item_id] = item

                        # Sync to catalog.db
                        try:
                            cat_db.upsert_dlc_item(
                                item_id=item_id,
                                title=item_id,
                                dlc_type=dlc_type,
                                archive_name=name,
                                archive_size=res.size,
                                public_url=res.path or catalog_url,
                                sha256=res.sha256,
                                md5=res.md5
                            )
                        except Exception:
                            pass

                self.set_idle(f"Каталог обновлён: загружено {len(self.dlc_items)} дополнений.")
            except YandexRateLimitError:
                self.set_error("Превышен лимит запросов к Яндекс.Диску (код 429). Повторите позже.")
            except NetworkError as e:
                self.set_error(f"Сетевая ошибка при подключении к облаку: {e}")
            except Exception as e:
                logger.error(f"Failed to refresh catalog: {e}")
                self.set_error(f"Ошибка загрузки каталога: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def start_download_selected(self) -> None:
        """
        Starts downloading all selected DLC items that are in AVAILABLE status.
        """
        if self.operation_state.is_busy:
            return

        selected = [item for item in self.dlc_items.values() if item.selected and item.status == DLCStatus.AVAILABLE]
        if not selected:
            logger.info("No items selected for download.")
            return

        def _worker():
            self.set_busy(TaskState.DOWNLOADING, f"Загрузка {len(selected)} архивов...")
            try:
                client = self.get_yandex_client()
                downloader = self.get_download_manager()
                from core.downloader import DownloadTask, DownloadStatus, DownloadProgress

                submitted_tasks = []

                for item in selected:
                    with self._lock:
                        item.status = DLCStatus.DOWNLOADING
                        item.download_progress = 0.0
                        self.active_downloads[item.item_id] = item
                    self.notify_change()

                    # Retrieve direct link
                    try:
                        direct_url = client.get_public_download_url(
                            public_key=self.branding.primary_dlc_folder,
                            path="/" + item.archive_name
                        )
                    except Exception as e:
                        logger.error(f"Failed to get direct URL for {item.archive_name}: {e}")
                        with self._lock:
                            item.status = DLCStatus.ERROR
                            self.active_downloads.pop(item.item_id, None)
                        self.set_error(f"Не удалось получить ссылку для {item.title}: {e}")
                        continue

                    task = DownloadTask(
                        url=direct_url,
                        destination_dir=self.context.downloads_dir,
                        filename=item.archive_name,
                        task_id=item.item_id,
                        expected_size=item.archive_size if item.archive_size and item.archive_size > 0 else None,
                        expected_sha256=item.sha256 or None,
                    )

                    def make_progress_handler(target_item: DLCUIItem):
                        last_notify_time = [0.0]

                        def on_progress(p: DownloadProgress):
                            with self._lock:
                                if p.status == DownloadStatus.DOWNLOADING:
                                    target_item.status = DLCStatus.DOWNLOADING
                                    target_item.download_progress = p.progress_percent / 100.0
                                    target_item.download_speed = p.speed_bps
                                    target_item.eta_seconds = int(p.eta_seconds) if p.eta_seconds is not None else None
                                elif p.status == DownloadStatus.COMPLETED:
                                    target_item.status = DLCStatus.DOWNLOADED
                                    target_item.download_progress = 1.0
                                    target_item.download_speed = 0.0
                                    target_item.eta_seconds = 0
                                    self.active_downloads.pop(target_item.item_id, None)
                                elif p.status == DownloadStatus.CANCELLED:
                                    target_item.status = DLCStatus.AVAILABLE
                                    target_item.download_progress = 0.0
                                    target_item.download_speed = 0.0
                                    self.active_downloads.pop(target_item.item_id, None)
                                elif p.status == DownloadStatus.FAILED:
                                    target_item.status = DLCStatus.ERROR
                                    target_item.download_progress = 0.0
                                    target_item.download_speed = 0.0
                                    self.active_downloads.pop(target_item.item_id, None)

                            now = time.time()
                            is_terminal = p.status in (DownloadStatus.COMPLETED, DownloadStatus.CANCELLED, DownloadStatus.FAILED)
                            if is_terminal or (now - last_notify_time[0] >= 0.25):
                                last_notify_time[0] = now
                                self.notify_change()
                        return on_progress

                    task_id = downloader.submit(task, progress_callback=make_progress_handler(item))
                    submitted_tasks.append((item, task_id))

                if not submitted_tasks:
                    self.set_idle("Нет доступных задач для загрузки.")
                    return

                # Wait for all submitted tasks in this batch to finish
                terminal_statuses = {DownloadStatus.COMPLETED, DownloadStatus.CANCELLED, DownloadStatus.FAILED}
                while True:
                    all_done = True
                    for it, tid in submitted_tasks:
                        prog = downloader.get_progress(tid)
                        if prog is None or prog.status not in terminal_statuses:
                            all_done = False
                            break
                    if all_done:
                        break
                    time.sleep(0.1)

                # Finalize states
                failed_items = []
                cancelled_items = []
                completed_items = []
                for it, tid in submitted_tasks:
                    prog = downloader.get_progress(tid)
                    if prog:
                        if prog.status == DownloadStatus.COMPLETED:
                            completed_items.append(it)
                            with self._lock:
                                it.status = DLCStatus.DOWNLOADED
                                it.download_progress = 1.0
                                self.active_downloads.pop(it.item_id, None)
                        elif prog.status == DownloadStatus.CANCELLED:
                            cancelled_items.append(it)
                            with self._lock:
                                it.status = DLCStatus.AVAILABLE
                                it.download_progress = 0.0
                                self.active_downloads.pop(it.item_id, None)
                        elif prog.status == DownloadStatus.FAILED:
                            failed_items.append((it, prog.error_message))
                            with self._lock:
                                it.status = DLCStatus.ERROR
                                it.download_progress = 0.0
                                self.active_downloads.pop(it.item_id, None)
                self.notify_change()

                if failed_items:
                    first_err = failed_items[0][1] or "Ошибка загрузки"
                    self.set_error(f"Сбой загрузки ({len(failed_items)} из {len(submitted_tasks)}): {first_err}")
                elif cancelled_items and not completed_items:
                    self.set_idle("Загрузка отменена пользователем.")
                else:
                    self.set_idle(f"Загрузка {len(completed_items)} архивов успешно завершена.")

            except DownloadCancelledError:
                self.set_idle("Загрузка отменена пользователем.")
            except HashMismatchError as e:
                self.set_error(f"Контрольная сумма архива повреждена: {e}")
            except Exception as e:
                logger.error(f"Download error: {e}")
                self.set_error(f"Ошибка загрузки: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def perform_dry_run(self, item_id: str, archive_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """
        Executes Dry Run simulation for a specific DLC archive.
        Returns plan dictionary (creates, overwrites, required_backup_size) with 0 disk mutations.
        """
        if not self.game_info.found or not self.game_info.path:
            raise GameNotFoundError("Игра The Sims 4 не найдена. Укажите путь к игре.")

        target_archive: Optional[Path] = None
        if archive_path:
            target_archive = Path(archive_path)
            if not target_archive.is_file():
                raise Sims4ManagerError(f"Файл архива не найден: '{archive_path}'")
        else:
            item = self.dlc_items.get(item_id)
            if not item:
                # Check directly in downloads_dir
                direct = self.context.downloads_dir / f"{item_id}.zip"
                if direct.is_file():
                    target_archive = direct
                else:
                    raise Sims4ManagerError(f"DLC '{item_id}' не найдено в каталоге или папке загрузок.")
            else:
                archive_p = self.context.downloads_dir / item.archive_name
                if not archive_p.is_file():
                    raise Sims4ManagerError(f"Архив '{item.archive_name}' не скачан на диск.")
                target_archive = archive_p

        from core.installer import DLCInstaller
        installer = DLCInstaller(
            state_db=self.get_state_db(),
            app_context=self.context
        )
        return installer.dry_run(
            dlc_code=item_id,
            archive_path=str(target_archive),
            game_root=self.game_info.path
        )

    def run_post_install_helper(self, user_agreed: bool) -> None:
        """
        Executes Post Install Helper adhering strictly to User Consent Gate.
        """
        if not self.game_info.found or not self.game_info.path:
            raise GameNotFoundError("Игра The Sims 4 не найдена. Укажите путь к игре.")

        from core.post_install_helper import PostInstallHelper
        helper = PostInstallHelper(
            game_root=Path(self.game_info.path),
            helper_download_url=self.branding.helper_archive_url,
            downloads_dir=self.context.downloads_dir,
            consent_callback=lambda: user_agreed
        )
        helper.run()

    def check_crash_recovery(self) -> Optional[str]:
        """
        Checks state.db for incomplete or interrupted transactions and reports status.
        """
        try:
            db = self.get_state_db()
            incomplete = db.list_active_or_incomplete_transactions()
            if incomplete:
                return f"Обнаружено {len(incomplete)} незавершённых транзакций. Рекомендуется аварийное восстановление."
            return "Транзакционный журнал согласован, незавершённых транзакций нет."
        except Exception as e:
            logger.error(f"Error checking crash recovery: {e}")
            return f"Ошибка проверки журнала транзакций: {e}"

    def translate_exception(self, exc: Exception) -> str:
        """
        Translates backend exceptions into friendly, actionable Russian messages.
        """
        if isinstance(exc, Sims4UserDirectoryNotFoundError):
            return str(exc)
        elif isinstance(exc, GameNotFoundError):
            return "Каталог The Sims 4 не найден. Проверьте установку игры или укажите путь вручную."
        elif isinstance(exc, UnsupportedFilesystemError):
            return "Файловая система диска не поддерживается. Требуется том NTFS."
        elif isinstance(exc, ReparsePointError):
            return "Обнаружена символическая ссылка или точка повторной обработки (Reparse Point). Операция отменена в целях безопасности."
        elif isinstance(exc, PathTraversalError):
            return "Архив содержит недопустимые пути за пределами каталога игры (Path Traversal). Файл заблокирован."
        elif isinstance(exc, BlockedBinaryError):
            return "Архив DLC содержит запрещённые исполняемые файлы (.exe/.dll). Установка заблокирована."
        elif isinstance(exc, CorruptedArchiveError):
            return "Архив повреждён или имеет нулевой размер. Пожалуйста, скачайте файл заново."
        elif isinstance(exc, HashMismatchError):
            return "Контрольная сумма скачанного файла не совпадает с оригиналом. Возможно повреждение при загрузке."
        elif isinstance(exc, YandexRateLimitError):
            return "Превышен лимит запросов к облаку Яндекс.Диска. Подождите несколько минут."
        elif isinstance(exc, NetworkError):
            return "Сетевой сбой при связи с облаком. Проверьте подключение к Интернету."
        elif isinstance(exc, LockContextError):
            return "Ошибка блокировки файловой транзакции. Другой процесс или UAC удерживает блокировку."
        elif isinstance(exc, RecoveryConflictError):
            return "Конфликт восстановления: целевой файл был изменён сторонней программой."
        elif isinstance(exc, DatabaseCorruptionError):
            return "База данных состояния повреждена. Требуется резервное восстановление."
        elif isinstance(exc, Sims4ManagerError):
            return str(exc)
        elif isinstance(exc, (ValueError, FileNotFoundError)):
            return str(exc)
        return f"Произошла непредвиденная ошибка: {exc}"
