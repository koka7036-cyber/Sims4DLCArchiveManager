"""
Yandex.Disk REST API client for public and private cloud resources.
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 2, 3, 4 and Stage 3 tasks.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Generator, List, Optional
from urllib.parse import urlparse, urlunparse

from core._network_compat import requests, HTTPAdapter, Retry

from core.exceptions import (
    NetworkError,
    YandexAPIError,
    YandexAuthError,
    YandexRateLimitError,
    YandexResourceNotFoundError,
)
from core.logger import get_logger

logger = get_logger("core.yandex_client")

YANDEX_API_BASE_URL = "https://cloud-api.yandex.net/v1/disk"
DEFAULT_USER_AGENT = "Sims4DLCArchiveManager/1.1.5 (Windows NT 10.0; Win64; x64)"
DEFAULT_TIMEOUT = (10.0, 30.0)  # (connect, read) seconds

PUBLIC_URL_REGEX = re.compile(
    r"^https?://(disk\.yandex\.(?:ru|com|by|kz|uz)|yadi\.sk)/[di]/([a-zA-Z0-9_\-]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class YandexResource:
    """Represents a file or directory on Yandex.Disk with full metadata."""
    name: str
    path: str
    type: str  # 'file' or 'dir'
    size: int = 0
    mime_type: Optional[str] = None
    md5: Optional[str] = None
    sha256: Optional[str] = None
    public_key: Optional[str] = None
    created: Optional[str] = None
    modified: Optional[str] = None
    download_url: Optional[str] = None

    @property
    def is_file(self) -> bool:
        return self.type == "file"

    @property
    def is_dir(self) -> bool:
        return self.type == "dir"

    @property
    def is_archive(self) -> bool:
        lower_name = self.name.lower()
        return lower_name.endswith((".zip", ".7z", ".rar"))

    @classmethod
    def from_api_dict(cls, data: Dict[str, Any], public_key: Optional[str] = None) -> YandexResource:
        """Construct a YandexResource instance from Yandex REST API JSON object."""
        return cls(
            name=str(data.get("name", "")),
            path=str(data.get("path", "")),
            type=str(data.get("type", "file")),
            size=int(data.get("size", 0) or 0),
            mime_type=data.get("mime_type"),
            md5=data.get("md5"),
            sha256=data.get("sha256"),
            public_key=data.get("public_key") or public_key,
            created=data.get("created"),
            modified=data.get("modified"),
            download_url=data.get("file"),
        )


def clean_public_url(url: str) -> str:
    """
    Clean and normalize public Yandex Disk URL, stripping query parameters and fragments.
    Validates against recognized domain formats.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Public URL must be a non-empty string")

    stripped = url.strip()
    match = PUBLIC_URL_REGEX.match(stripped)
    if not match:
        # Check if already a pure key
        if re.match(r"^[a-zA-Z0-9_\-]+$", stripped):
            return stripped
        # Otherwise keep clean parsed URL without query/params
        parsed = urlparse(stripped)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid Yandex.Disk URL format: {url}")
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))

    parsed = urlparse(stripped)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


class YandexClient:
    """
    Client for interacting with Yandex.Disk REST API.
    Supports both public folder/file operations and authenticated owner operations.
    Includes deterministic exponential backoff retries on HTTP 429 and 5xx errors.
    """

    def __init__(
        self,
        oauth_token: Optional[str] = None,
        dpapi_token_bytes: Optional[bytes] = None,
        session: Optional[requests.Session] = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ) -> None:
        self.timeout = timeout
        self.oauth_token: Optional[str] = None

        if dpapi_token_bytes:
            from core.security import dpapi_unprotect
            raw = dpapi_unprotect(dpapi_token_bytes)
            self.oauth_token = raw.decode("utf-8").strip()
        elif oauth_token:
            self.oauth_token = oauth_token.strip()

        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            retry_strategy = Retry(
                total=max_retries,
                connect=max_retries,
                read=max_retries,
                status=max_retries,
                backoff_factor=backoff_factor,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET", "HEAD"],
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)

        self.session.headers.update({
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
        })

        if self.oauth_token:
            self.session.headers["Authorization"] = f"OAuth {self.oauth_token}"

    def _handle_response_errors(self, response: requests.Response) -> None:
        """Map HTTP error status codes to typed exceptions."""
        if response.status_code < 400:
            return

        error_msg = f"HTTP {response.status_code}"
        error_code = None
        try:
            body = response.json()
            if isinstance(body, dict):
                error_msg = body.get("message") or body.get("description") or error_msg
                error_code = body.get("error")
        except Exception:
            error_msg = response.text or error_msg

        logger.warning(
            "Yandex.Disk API error status=%d error_code=%s message=%s",
            response.status_code,
            error_code,
            error_msg,
        )

        if response.status_code == 404:
            raise YandexResourceNotFoundError(
                f"Resource not found on Yandex.Disk: {error_msg}",
                status_code=404,
                error_code=error_code,
            )
        if response.status_code == 429:
            raise YandexRateLimitError(
                f"Yandex.Disk rate limit exceeded: {error_msg}",
                status_code=429,
                error_code=error_code,
            )
        if response.status_code in (401, 403):
            raise YandexAuthError(
                f"Yandex.Disk authentication/authorization failure: {error_msg}",
                status_code=response.status_code,
                error_code=error_code,
            )

        raise YandexAPIError(
            f"Yandex.Disk API request failed: {error_msg}",
            status_code=response.status_code,
            error_code=error_code,
        )

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> requests.Response:
        """Execute HTTP request with unified exception translation."""
        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
            self._handle_response_errors(response)
            return response
        except (requests.ConnectionError, requests.Timeout) as e:
            logger.error("Network communication failure: %s", str(e))
            raise NetworkError(f"Network error communicating with Yandex.Disk: {e}") from e
        except (YandexAPIError, NetworkError):
            raise
        except Exception as e:
            logger.error("Unexpected error in YandexClient request: %s", str(e))
            raise NetworkError(f"Unexpected request error: {e}") from e

    # ==========================================================================
    # Public Resources API
    # ==========================================================================

    def get_public_resource_meta(
        self,
        public_key: str,
        path: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fetch metadata for a public resource or folder page.
        Endpoint: GET /v1/disk/public/resources
        """
        clean_key = clean_public_url(public_key)
        url = f"{YANDEX_API_BASE_URL}/public/resources"
        params: Dict[str, Any] = {
            "public_key": clean_key,
            "limit": limit,
            "offset": offset,
        }
        if path:
            params["path"] = path if path.startswith("/") else f"/{path}"
        if sort:
            params["sort"] = sort

        resp = self._request("GET", url, params=params)
        return resp.json()

    def get_public_download_url(
        self,
        public_key: str,
        path: Optional[str] = None,
    ) -> str:
        """
        Obtain a direct download link for a public resource or file inside a public folder.
        Endpoint: GET /v1/disk/public/resources/download
        """
        clean_key = clean_public_url(public_key)
        url = f"{YANDEX_API_BASE_URL}/public/resources/download"
        params: Dict[str, Any] = {"public_key": clean_key}
        if path:
            params["path"] = path if path.startswith("/") else f"/{path}"

        resp = self._request("GET", url, params=params)
        data = resp.json()
        download_url = data.get("href")
        if not download_url:
            raise YandexAPIError("API response did not contain download href")
        return str(download_url)

    def get_file_info(
        self,
        public_key: str,
        path: Optional[str] = None,
    ) -> YandexResource:
        """
        Get metadata for a specific public file (e.g., 'dlc помощник.zip' or a specific file in folder).
        """
        meta = self.get_public_resource_meta(public_key, path=path, limit=1)
        res_type = meta.get("type", "file")
        if res_type == "file":
            return YandexResource.from_api_dict(meta, public_key=public_key)

        # If it's a directory and path is specified, inspect items or raise
        embedded = meta.get("_embedded", {})
        items = embedded.get("items", [])
        if items and len(items) == 1 and items[0].get("type") == "file":
            return YandexResource.from_api_dict(items[0], public_key=public_key)

        return YandexResource.from_api_dict(meta, public_key=public_key)

    def list_public_folder_items(
        self,
        public_key: str,
        path: Optional[str] = None,
        recursive: bool = False,
        batch_size: int = 100,
    ) -> List[YandexResource]:
        """
        Fetch all items in a public folder, transparently handling pagination (limit/offset)
        and optional recursive directory traversal.
        """
        clean_key = clean_public_url(public_key)
        results: List[YandexResource] = []

        current_offset = 0
        total_items = None

        while True:
            data = self.get_public_resource_meta(
                public_key=clean_key,
                path=path,
                limit=batch_size,
                offset=current_offset,
            )

            res_type = data.get("type", "file")
            if res_type == "file":
                results.append(YandexResource.from_api_dict(data, public_key=clean_key))
                return results

            embedded = data.get("_embedded")
            if not embedded:
                break

            total_items = embedded.get("total", 0)
            items = embedded.get("items", [])
            if not items:
                break

            for item_data in items:
                res = YandexResource.from_api_dict(item_data, public_key=clean_key)
                results.append(res)
                if recursive and res.is_dir:
                    child_path = res.path
                    child_items = self.list_public_folder_items(
                        public_key=clean_key,
                        path=child_path,
                        recursive=True,
                        batch_size=batch_size,
                    )
                    results.extend(child_items)

            current_offset += len(items)
            if current_offset >= total_items or len(items) < batch_size:
                break

        return results

    # ==========================================================================
    # Private / Authenticated Resources API (Owner Mode)
    # ==========================================================================

    def get_private_resource_meta(
        self,
        path: str,
        limit: int = 50,
        offset: int = 0,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fetch metadata for a resource in owner's personal Yandex.Disk.
        Requires OAuth authorization.
        Endpoint: GET /v1/disk/resources
        """
        if not self.oauth_token:
            raise YandexAuthError("OAuth token required for private disk access")

        url = f"{YANDEX_API_BASE_URL}/resources"
        params: Dict[str, Any] = {
            "path": path if path.startswith("disk:/") or path.startswith("/") else f"/{path}",
            "limit": limit,
            "offset": offset,
        }
        if sort:
            params["sort"] = sort

        resp = self._request("GET", url, params=params)
        return resp.json()

    def get_private_download_url(self, path: str) -> str:
        """
        Obtain a direct download link for a private resource on owner's Yandex.Disk.
        Requires OAuth authorization.
        Endpoint: GET /v1/disk/resources/download
        """
        if not self.oauth_token:
            raise YandexAuthError("OAuth token required for private disk access")

        url = f"{YANDEX_API_BASE_URL}/resources/download"
        params = {"path": path if path.startswith("disk:/") or path.startswith("/") else f"/{path}"}

        resp = self._request("GET", url, params=params)
        data = resp.json()
        download_url = data.get("href")
        if not download_url:
            raise YandexAPIError("API response did not contain download href")
        return str(download_url)
