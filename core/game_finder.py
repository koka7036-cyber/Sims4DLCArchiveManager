"""
Game location detector, volume & NTFS verification, and deep DLC scanner.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 5.3 and 9.
"""

import os
import sys
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Any

from core.exceptions import (
    UnsupportedFilesystemError,
    FilesystemError,
    Sims4ManagerError,
)
from core.logger import get_logger

logger = get_logger("game_finder")

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32

    # GetVolumePathNameW
    kernel32.GetVolumePathNameW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetVolumePathNameW.restype = wintypes.BOOL

    # GetVolumeInformationW
    kernel32.GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetVolumeInformationW.restype = wintypes.BOOL

# Registry keys to inspect for The Sims 4 installation path
REGISTRY_SIMS4_PATHS = [
    (r"SOFTWARE\Maxis\The Sims 4", "Install Dir"),
    (r"SOFTWARE\Maxis\The Sims 4", "InstallDir"),
    (r"SOFTWARE\Electronic Arts\EA Games\The Sims 4", "Install Dir"),
    (r"SOFTWARE\Electronic Arts\EA Games\The Sims 4", "InstallDir"),
    (r"SOFTWARE\Electronic Arts\EA Games\The Sims 4", "Path"),
]

# Steam The Sims 4 AppID
STEAM_SIMS4_APPID = "1222670"

# DLC Pattern
DLC_PATTERN = re.compile(r"^(EP|GP|SP|FP)(\d{2})$", re.IGNORECASE)


@dataclass
class GameInstallation:
    """Represents a located The Sims 4 game directory and volume metadata."""
    path: Path
    volume_path: str
    filesystem: str
    source: str
    version: Optional[str] = None
    is_valid: bool = True


@dataclass
class DLCInfo:
    """Represents deep scan analysis of a specific DLC folder."""
    dlc_code: str
    dlc_type: str  # EP, GP, SP, FP
    dlc_number: int
    folder_path: Path
    is_installed: bool
    package_count: int
    total_size_bytes: int
    package_files: List[Path] = field(default_factory=list)
    has_installer: bool = False
    has_delta: bool = False


@dataclass
class GameScanReport:
    """Comprehensive report containing installation details and all scanned DLCs."""
    installation: GameInstallation
    dlcs: Dict[str, DLCInfo]
    installed_count: int
    total_dlcs_scanned: int


# ==============================================================================
# Volume and Filesystem Verification
# ==============================================================================

# Mock hook for filesystem tests on non-Windows or custom test injection
_FS_OVERRIDE_REGISTRY: Dict[str, str] = {}


def set_test_filesystem_override(path_prefix: str, filesystem_name: str) -> None:
    """Injects a mock filesystem response for the given path prefix (for tests)."""
    _FS_OVERRIDE_REGISTRY[os.path.abspath(path_prefix)] = filesystem_name


def clear_test_filesystem_overrides() -> None:
    """Clears all mock filesystem overrides."""
    _FS_OVERRIDE_REGISTRY.clear()


def get_volume_path(target_path: str) -> str:
    """
    Determines the volume mount point for the given path using GetVolumePathNameW on Windows.
    Returns the volume path (e.g. 'D:\\' or 'C:\\').
    """
    norm_path = os.path.abspath(target_path)

    # Check test override
    for prefix in sorted(_FS_OVERRIDE_REGISTRY.keys(), key=len, reverse=True):
        if norm_path.startswith(prefix):
            return prefix

    if IS_WINDOWS:
        buf = ctypes.create_unicode_buffer(512)
        res = kernel32.GetVolumePathNameW(norm_path, buf, 512)
        if not res:
            err = kernel32.GetLastError()
            raise FilesystemError(f"GetVolumePathNameW failed for '{norm_path}' with error code {err}")
        return buf.value
    else:
        # Cross-platform fallback for testing: root directory of partition
        drive, _ = os.path.splitdrive(norm_path)
        if drive:
            return drive + "\\"
        return "/"


def get_volume_filesystem(volume_path: str) -> str:
    """
    Queries the filesystem name (e.g., 'NTFS', 'FAT32', 'exFAT') for the volume.
    Mandated by PROJECT_SPEC.md section 5.3: must use GetVolumeInformationW.
    """
    norm_vol = os.path.abspath(volume_path)

    # Check test override
    for prefix, fs_name in _FS_OVERRIDE_REGISTRY.items():
        if norm_vol.startswith(prefix) or prefix.startswith(norm_vol):
            return fs_name

    if IS_WINDOWS:
        # Ensure trailing backslash for root volume path in GetVolumeInformationW
        win_vol_path = volume_path
        if not win_vol_path.endswith("\\"):
            win_vol_path += "\\"

        fs_name_buf = ctypes.create_unicode_buffer(256)
        volume_name_buf = ctypes.create_unicode_buffer(256)
        serial_number = wintypes.DWORD()
        max_component_len = wintypes.DWORD()
        flags = wintypes.DWORD()

        res = kernel32.GetVolumeInformationW(
            win_vol_path,
            volume_name_buf,
            256,
            ctypes.byref(serial_number),
            ctypes.byref(max_component_len),
            ctypes.byref(flags),
            fs_name_buf,
            256,
        )
        if not res:
            err = kernel32.GetLastError()
            raise FilesystemError(f"GetVolumeInformationW failed for '{win_vol_path}' with error code {err}")
        return fs_name_buf.value
    else:
        # Default mock on Linux if not explicitly overridden in tests
        return "NTFS"


def verify_ntfs_filesystem(path: str) -> Tuple[str, str]:
    """
    Verifies that the target path resides on an NTFS filesystem.
    Raises UnsupportedFilesystemError if the filesystem is not NTFS (e.g., FAT32, exFAT, ReFS).
    Mandated by PROJECT_SPEC.md section 5.3:
    "Если выходной параметр lpFileSystemNameBuffer возвращает значение, отличное от L'NTFS'
    приложение немедленно блокирует операцию... возбуждая исключение UnsupportedFilesystemError".
    """
    volume = get_volume_path(path)
    fs_name = get_volume_filesystem(volume)

    if fs_name.upper() != "NTFS":
        raise UnsupportedFilesystemError(
            f"Directory '{path}' is on an unsupported filesystem '{fs_name}' (Volume: '{volume}'). "
            f"The Sims 4 DLC Manager strictly requires NTFS for transactional handle operations and 128-bit FileId integrity."
        )

    return volume, fs_name


# ==============================================================================
# Valve KeyValues (libraryfolders.vdf) Parser
# ==============================================================================

def parse_steam_libraryfolders_vdf(vdf_content: str) -> List[Dict[str, Any]]:
    """
    Parses Steam's libraryfolders.vdf file content and returns a list of library dicts
    containing 'path' and set of installed 'apps'.
    """
    libraries: List[Dict[str, Any]] = []
    # Tokenize VDF strings and braces
    tokens = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"|([{}]|[^\s{}"]+)', vdf_content)
    clean_tokens = [t[0] if t[0] else t[1] for t in tokens if t[0] or t[1]]

    # Parse KeyValues structure
    idx = 0
    total = len(clean_tokens)

    def parse_object() -> Dict[str, Any]:
        nonlocal idx
        obj: Dict[str, Any] = {}
        while idx < total:
            token = clean_tokens[idx]
            if token == "}":
                idx += 1
                return obj
            key = token
            idx += 1
            if idx >= total:
                break
            val_token = clean_tokens[idx]
            if val_token == "{":
                idx += 1
                obj[key] = parse_object()
            else:
                obj[key] = val_token
                idx += 1
        return obj

    root = {}
    while idx < total:
        key = clean_tokens[idx]
        idx += 1
        if idx < total and clean_tokens[idx] == "{":
            idx += 1
            root[key] = parse_object()

    # Extract libraries from parsed structure
    lib_root = root.get("libraryfolders", root)
    if isinstance(lib_root, dict):
        for lib_key, lib_val in lib_root.items():
            if isinstance(lib_val, dict):
                lib_path = lib_val.get("path")
                if lib_path:
                    # Clean escaped backslashes
                    clean_path = lib_path.replace("\\\\", "\\")
                    apps_dict = lib_val.get("apps", {})
                    apps_set = set(apps_dict.keys()) if isinstance(apps_dict, dict) else set()
                    libraries.append({
                        "path": clean_path,
                        "apps": apps_set
                    })

    return libraries


# ==============================================================================
# Game Structure Verification
# ==============================================================================

def is_valid_sims4_root(path: Path) -> bool:
    """
    Checks if the given directory contains standard The Sims 4 indicators:
    - Game/Bin/TS4_x64.exe or Game/Bin/TS4.exe or Game/Bin_LE/TS4.exe
    - Data/Client or Data/
    """
    if not path.is_dir():
        return False

    bin_markers = [
        path / "Game" / "Bin" / "TS4_x64.exe",
        path / "Game" / "Bin" / "TS4.exe",
        path / "Game" / "Bin_LE" / "TS4.exe",
        path / "TS4_x64.exe",
        path / "TS4.exe",
    ]
    has_bin = any(m.exists() for m in bin_markers)

    data_markers = [
        path / "Data",
        path / "Data" / "Client",
        path / "Data" / "Simulation",
    ]
    has_data = any(m.exists() for m in data_markers)

    # Valid if it contains executable binary or Data structure
    return has_bin or (has_data and (path / "Game").exists()) or has_bin


# ==============================================================================
# Game Location Engine
# ==============================================================================

class GameFinder:
    """
    Automated discovery and validation engine for The Sims 4.
    Searches Windows Registry, Steam library folders, and EA Desktop/Origin.
    """

    def __init__(self, registry_backend: Optional[Any] = None):
        """
        Args:
            registry_backend: Optional mock winreg module for isolated testing.
        """
        self._registry_backend = registry_backend

    def find_game(self) -> Optional[GameInstallation]:
        """
        Searches all available sources in priority order:
        1. Windows Registry (Maxis / Electronic Arts)
        2. Steam library folders (AppID 1222670)
        3. EA Desktop / Origin default directories
        """
        # 1. Registry
        found = self.search_registry()
        if found and is_valid_sims4_root(found):
            vol, fs = verify_ntfs_filesystem(str(found))
            return GameInstallation(path=found, volume_path=vol, filesystem=fs, source="registry")

        # 2. Steam
        found = self.search_steam()
        if found and is_valid_sims4_root(found):
            vol, fs = verify_ntfs_filesystem(str(found))
            return GameInstallation(path=found, volume_path=vol, filesystem=fs, source="steam")

        # 3. EA Desktop / Origin
        found = self.search_ea_origin()
        if found and is_valid_sims4_root(found):
            vol, fs = verify_ntfs_filesystem(str(found))
            return GameInstallation(path=found, volume_path=vol, filesystem=fs, source="ea_origin")

        return None

    def validate_custom_path(self, custom_path: str) -> GameInstallation:
        """
        Validates a user-selected path:
        1. Checks folder existence and Sims 4 markers.
        2. Strictly verifies NTFS filesystem via GetVolumeInformationW.
        """
        path = Path(custom_path).resolve()
        if not path.is_dir():
            raise FilesystemError(f"Specified path does not exist or is not a directory: '{path}'")

        if not is_valid_sims4_root(path):
            raise FilesystemError(f"Specified folder is not a valid The Sims 4 installation: '{path}'")

        volume, fs_name = verify_ntfs_filesystem(str(path))
        return GameInstallation(
            path=path,
            volume_path=volume,
            filesystem=fs_name,
            source="manual"
        )

    def search_registry(self) -> Optional[Path]:
        """
        Queries Windows Registry (HKLM and HKCU, 64-bit and 32-bit views).
        """
        reg = self._registry_backend
        if reg is None:
            if not IS_WINDOWS:
                return None
            try:
                import winreg
                reg = winreg
            except ImportError:
                return None

        # Hives to search
        hives = [
            (reg.HKEY_LOCAL_MACHINE, "HKLM"),
            (reg.HKEY_CURRENT_USER, "HKCU"),
        ]

        # Flags for 64-bit and 32-bit registry views
        access_flags = [0]
        if hasattr(reg, "KEY_WOW64_64KEY"):
            access_flags.append(reg.KEY_WOW64_64KEY)
        if hasattr(reg, "KEY_WOW64_32KEY"):
            access_flags.append(reg.KEY_WOW64_32KEY)

        for hive_key, hive_name in hives:
            for subkey, value_name in REGISTRY_SIMS4_PATHS:
                for wow_flag in access_flags:
                    try:
                        sam = reg.KEY_READ | wow_flag if hasattr(reg, "KEY_READ") else wow_flag
                        with reg.OpenKey(hive_key, subkey, 0, sam) as key:
                            val, _ = reg.QueryValueEx(key, value_name)
                            if val and isinstance(val, str):
                                candidate = Path(val.strip().rstrip("\\/")).resolve()
                                if candidate.exists():
                                    return candidate
                    except (OSError, FileNotFoundError, PermissionError):
                        continue

        return None

    def search_steam(self, steam_root: Optional[str] = None) -> Optional[Path]:
        """
        Searches Steam installation and parses libraryfolders.vdf for The Sims 4 (AppID 1222670).
        """
        candidates: List[Path] = []
        if steam_root:
            candidates.append(Path(steam_root))
        else:
            if IS_WINDOWS:
                program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
                candidates.extend([
                    Path(program_files_x86) / "Steam",
                    Path(r"C:\Program Files\Steam"),
                    Path(r"C:\Steam"),
                ])
                # Check Steam registry
                reg = self._registry_backend
                if reg or IS_WINDOWS:
                    if reg is None:
                        import winreg
                        reg = winreg
                    try:
                        with reg.OpenKey(reg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                            steam_path, _ = reg.QueryValueEx(key, "SteamPath")
                            if steam_path:
                                candidates.insert(0, Path(steam_path))
                    except Exception:
                        pass

        for root in candidates:
            vdf_path = root / "steamapps" / "libraryfolders.vdf"
            if not vdf_path.is_file():
                continue

            try:
                with open(vdf_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                libs = parse_steam_libraryfolders_vdf(content)
                for lib in libs:
                    # Check if Sims 4 AppID is recorded in library apps
                    if STEAM_SIMS4_APPID in lib["apps"]:
                        candidate_game = Path(lib["path"]) / "steamapps" / "common" / "The Sims 4"
                        if candidate_game.exists():
                            return candidate_game

                    # Also check direct folder existence in common
                    candidate_game = Path(lib["path"]) / "steamapps" / "common" / "The Sims 4"
                    if candidate_game.exists():
                        return candidate_game
            except Exception as e:
                logger.warning("Failed parsing Steam libraryfolders.vdf at %s: %s", vdf_path, e)

        return None

    def search_ea_origin(self) -> Optional[Path]:
        """
        Searches standard EA Desktop and Origin Game directories.
        """
        candidates: List[Path] = []
        if IS_WINDOWS:
            program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
            program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
            candidates.extend([
                Path(program_files) / "EA Games" / "The Sims 4",
                Path(program_files_x86) / "Origin Games" / "The Sims 4",
                Path(program_files) / "Origin Games" / "The Sims 4",
                Path(r"C:\Program Files\EA Games\The Sims 4"),
                Path(r"C:\Program Files (x86)\Origin Games\The Sims 4"),
                Path(r"D:\Games\The Sims 4"),
                Path(r"D:\The Sims 4"),
            ])

        for path in candidates:
            if path.exists() and is_valid_sims4_root(path):
                return path

        return None


# ==============================================================================
# Deep DLC Scanner
# ==============================================================================

class DeepDLCScanner:
    """
    Deep filesystem scanner for The Sims 4 DLC folders.
    Enforces PROJECT_SPEC.md section 9.3:
    - Empty folders are NOT considered installed DLCs.
    - Scans for actual presence of valid .package files with st_size > 0.
    """

    @staticmethod
    def scan_dlc_folder(dlc_folder_path: Path) -> DLCInfo:
        """
        Inspects an individual DLC folder (e.g. EP01) and returns DLCInfo.
        """
        name = dlc_folder_path.name.upper()
        match = DLC_PATTERN.match(name)

        if match:
            dlc_type = match.group(1).upper()
            dlc_num = int(match.group(2))
        else:
            dlc_type = "UNKNOWN"
            dlc_num = 0

        package_files: List[Path] = []
        total_size = 0

        if dlc_folder_path.is_dir():
            for root, _, files in os.walk(dlc_folder_path):
                for f in files:
                    if f.lower().endswith(".package"):
                        p = Path(root) / f
                        try:
                            st = p.stat()
                            # Non-zero size required per section 9.3
                            if st.st_size > 0:
                                package_files.append(p)
                                total_size += st.st_size
                        except OSError:
                            continue

        has_installer = (dlc_folder_path / "__Installer").is_dir()
        has_delta = (dlc_folder_path / "Delta").is_dir()

        # Mandated by section 9.3:
        # "Наличие пустой папки (например, EP01) не считается признаком установленного дополнения.
        # Выполняется рекурсивное сканирование содержимого папки дополнения на фактическое
        # присутствие основных файлов игровых архивов .package с ненулевым размером."
        is_installed = len(package_files) > 0

        return DLCInfo(
            dlc_code=name,
            dlc_type=dlc_type,
            dlc_number=dlc_num,
            folder_path=dlc_folder_path,
            is_installed=is_installed,
            package_count=len(package_files),
            total_size_bytes=total_size,
            package_files=package_files,
            has_installer=has_installer,
            has_delta=has_delta,
        )

    @classmethod
    def scan_game_installation(cls, game_installation: GameInstallation) -> GameScanReport:
        """
        Scans all DLC folders in the given game installation directory.
        """
        game_root = game_installation.path
        dlcs: Dict[str, DLCInfo] = {}

        if game_root.is_dir():
            for entry in game_root.iterdir():
                if entry.is_dir() and DLC_PATTERN.match(entry.name):
                    dlc_info = cls.scan_dlc_folder(entry)
                    dlcs[dlc_info.dlc_code] = dlc_info

        installed_count = sum(1 for d in dlcs.values() if d.is_installed)

        return GameScanReport(
            installation=game_installation,
            dlcs=dlcs,
            installed_count=installed_count,
            total_dlcs_scanned=len(dlcs),
        )
