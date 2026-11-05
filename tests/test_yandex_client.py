"""
Unit tests for YandexClient, public/private Cloud REST API, pagination, and error handling.
Conforms strictly to PROJECT_SPEC.md v1.1.5 Section 2, 3, 4 and Stage 3 requirements.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from core._network_compat import requests, HTTPAdapter, Retry

from core.exceptions import (
    NetworkError,
    YandexAPIError,
    YandexAuthError,
    YandexRateLimitError,
    YandexResourceNotFoundError,
)
from core.yandex_client import (
    DEFAULT_USER_AGENT,
    YANDEX_API_BASE_URL,
    YandexClient,
    YandexResource,
    clean_public_url,
)
from tests.conftest import IsolatedEnvironmentTestCase


class MockResponse:
    """Mock requests.Response for predictable testing."""

    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text or json.dumps(self._json_data)
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        if self._json_data is not None:
            return self._json_data
        raise json.JSONDecodeError("Expecting value", "", 0)


class TestYandexClient(IsolatedEnvironmentTestCase):
    """Test suite for Yandex.Disk API client."""

    def setUp(self):
        super().setUp()
        self.public_folder_url = "https://disk.yandex.ru/d/pYDq0CSIwqclFA"
        self.helper_archive_url = "https://disk.yandex.ru/d/RgVixKr-6QlLsQ"

    def test_clean_public_url_parsing(self):
        """Test URL normalization, query stripping, and validation."""
        # Standard public URL
        cleaned = clean_public_url("https://disk.yandex.ru/d/pYDq0CSIwqclFA")
        self.assertEqual(cleaned, "https://disk.yandex.ru/d/pYDq0CSIwqclFA")

        # Public URL with tracking query parameters
        cleaned_with_query = clean_public_url("https://disk.yandex.ru/d/pYDq0CSIwqclFA?w=1&token=xyz#section")
        self.assertEqual(cleaned_with_query, "https://disk.yandex.ru/d/pYDq0CSIwqclFA")

        # yadi.sk domain format
        yadi_cleaned = clean_public_url("https://yadi.sk/d/RgVixKr-6QlLsQ")
        self.assertEqual(yadi_cleaned, "https://yadi.sk/d/RgVixKr-6QlLsQ")

        # Raw key format
        raw_key = clean_public_url("pYDq0CSIwqclFA")
        self.assertEqual(raw_key, "pYDq0CSIwqclFA")

        # Invalid URL format
        with self.assertRaises(ValueError):
            clean_public_url("")
        with self.assertRaises(ValueError):
            clean_public_url("not a url at all !!!")

    def test_yandex_resource_dataclass(self):
        """Test YandexResource attributes and classification helpers."""
        file_res = YandexResource(
            name="EP01.rar",
            path="/EP01.rar",
            type="file",
            size=1048576,
            md5="d41d8cd98f00b204e9800998ecf8427e",
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        self.assertTrue(file_res.is_file)
        self.assertFalse(file_res.is_dir)
        self.assertTrue(file_res.is_archive)

        zip_res = YandexResource(name="dlc помощник.zip", path="/dlc помощник.zip", type="file")
        self.assertTrue(zip_res.is_archive)

        seven_zip = YandexResource(name="SP02.7z", path="/SP02.7z", type="file")
        self.assertTrue(seven_zip.is_archive)

        package_res = YandexResource(name="package.package", path="/package.package", type="file")
        self.assertFalse(package_res.is_archive)

        dir_res = YandexResource(name="EP01", path="/EP01", type="dir")
        self.assertTrue(dir_res.is_dir)
        self.assertFalse(dir_res.is_file)
        self.assertFalse(dir_res.is_archive)

    def test_get_public_resource_meta_success(self):
        """Test getting metadata for a public folder with custom limit/offset."""
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_response = MockResponse(200, {
            "name": "The Sims 4 DLC PLAYEROK FANFIRIEL",
            "type": "dir",
            "public_key": self.public_folder_url,
            "_embedded": {
                "total": 1,
                "limit": 20,
                "offset": 0,
                "items": [
                    {"name": "EP01.zip", "type": "file", "size": 1000, "path": "/EP01.zip"}
                ]
            }
        })
        mock_session.request.return_value = mock_response

        client = YandexClient(session=mock_session)
        meta = client.get_public_resource_meta(self.public_folder_url, limit=20, offset=0)

        self.assertEqual(meta["name"], "The Sims 4 DLC PLAYEROK FANFIRIEL")
        mock_session.request.assert_called_once()
        args, kwargs = mock_session.request.call_args
        self.assertEqual(kwargs["params"]["public_key"], self.public_folder_url)
        self.assertEqual(kwargs["params"]["limit"], 20)
        self.assertEqual(kwargs["params"]["offset"], 0)

    def test_get_file_info_single_file_helper(self):
        """Test retrieving metadata for the helper tool single archive 'dlc помощник.zip'."""
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_response = MockResponse(200, {
            "name": "dlc помощник.zip",
            "type": "file",
            "public_key": self.helper_archive_url,
            "path": "/",
            "size": 5242880,
            "md5": "0123456789abcdef0123456789abcdef",
            "sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        })
        mock_session.request.return_value = mock_response

        client = YandexClient(session=mock_session)
        resource = client.get_file_info(self.helper_archive_url)

        self.assertEqual(resource.name, "dlc помощник.zip")
        self.assertEqual(resource.type, "file")
        self.assertEqual(resource.size, 5242880)
        self.assertEqual(resource.md5, "0123456789abcdef0123456789abcdef")
        self.assertEqual(resource.sha256, "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789")
        self.assertTrue(resource.is_archive)

    def test_list_public_folder_items_pagination(self):
        """Test multi-page catalog traversal fetching all items transparently."""
        # 5 items total across 3 pages with batch_size=2
        page1 = MockResponse(200, {
            "name": "Root", "type": "dir",
            "_embedded": {
                "total": 5, "limit": 2, "offset": 0,
                "items": [
                    {"name": "EP01.zip", "type": "file", "size": 100, "path": "/EP01.zip"},
                    {"name": "EP02.zip", "type": "file", "size": 200, "path": "/EP02.zip"},
                ]
            }
        })
        page2 = MockResponse(200, {
            "name": "Root", "type": "dir",
            "_embedded": {
                "total": 5, "limit": 2, "offset": 2,
                "items": [
                    {"name": "GP01.zip", "type": "file", "size": 300, "path": "/GP01.zip"},
                    {"name": "SP01.zip", "type": "file", "size": 400, "path": "/SP01.zip"},
                ]
            }
        })
        page3 = MockResponse(200, {
            "name": "Root", "type": "dir",
            "_embedded": {
                "total": 5, "limit": 2, "offset": 4,
                "items": [
                    {"name": "FP01.zip", "type": "file", "size": 500, "path": "/FP01.zip"},
                ]
            }
        })

        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.request.side_effect = [page1, page2, page3]

        client = YandexClient(session=mock_session)
        items = client.list_public_folder_items(self.public_folder_url, batch_size=2)

        self.assertEqual(len(items), 5)
        self.assertEqual([item.name for item in items], ["EP01.zip", "EP02.zip", "GP01.zip", "SP01.zip", "FP01.zip"])
        self.assertEqual(mock_session.request.call_count, 3)

    def test_list_public_folder_recursive(self):
        """Test recursive listing traversing subdirectories."""
        root_resp = MockResponse(200, {
            "name": "Root", "type": "dir",
            "_embedded": {
                "total": 2, "limit": 10, "offset": 0,
                "items": [
                    {"name": "EP01", "type": "dir", "path": "/EP01"},
                    {"name": "readme.txt", "type": "file", "size": 50, "path": "/readme.txt"},
                ]
            }
        })
        sub_resp = MockResponse(200, {
            "name": "EP01", "type": "dir",
            "_embedded": {
                "total": 1, "limit": 10, "offset": 0,
                "items": [
                    {"name": "EP01.rar", "type": "file", "size": 1000, "path": "/EP01/EP01.rar"},
                ]
            }
        })

        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.request.side_effect = [root_resp, sub_resp]

        client = YandexClient(session=mock_session)
        items = client.list_public_folder_items(self.public_folder_url, recursive=True)

        self.assertEqual(len(items), 3)
        names = [item.name for item in items]
        self.assertIn("EP01", names)
        self.assertIn("readme.txt", names)
        self.assertIn("EP01.rar", names)

    def test_get_public_download_url_success(self):
        """Test obtaining direct download URL from /public/resources/download."""
        expected_href = "https://downloader.disk.yandex.ru/disk/download_token_12345/archive.zip"
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.request.return_value = MockResponse(200, {
            "href": expected_href,
            "method": "GET",
            "templated": False
        })

        client = YandexClient(session=mock_session)
        download_url = client.get_public_download_url(self.public_folder_url, path="/EP01.zip")

        self.assertEqual(download_url, expected_href)
        _, kwargs = mock_session.request.call_args
        self.assertEqual(kwargs["params"]["public_key"], self.public_folder_url)
        self.assertEqual(kwargs["params"]["path"], "/EP01.zip")

    def test_get_public_download_url_missing_href(self):
        """Test API response missing 'href' field raises YandexAPIError."""
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.request.return_value = MockResponse(200, {"error": "no_href"})

        client = YandexClient(session=mock_session)
        with self.assertRaises(YandexAPIError):
            client.get_public_download_url(self.public_folder_url)

    def test_error_status_mapping_404(self):
        """HTTP 404 should be translated to YandexResourceNotFoundError."""
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.request.return_value = MockResponse(404, {"message": "Resource not found", "error": "DiskNotFoundError"})

        client = YandexClient(session=mock_session)
        with self.assertRaises(YandexResourceNotFoundError) as ctx:
            client.get_public_resource_meta("https://disk.yandex.ru/d/invalid_key")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_error_status_mapping_429(self):
        """HTTP 429 should be translated to YandexRateLimitError."""
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.request.return_value = MockResponse(429, {"message": "Rate limit exceeded", "error": "TooManyRequests"})

        client = YandexClient(session=mock_session)
        with self.assertRaises(YandexRateLimitError) as ctx:
            client.get_public_resource_meta(self.public_folder_url)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_error_status_mapping_401_403(self):
        """HTTP 401 or 403 should be translated to YandexAuthError."""
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_response = MockResponse(401, {"message": "Unauthorized", "error": "UnauthorizedError"})
        mock_session.request.return_value = mock_response

        client = YandexClient(oauth_token="invalid_token", session=mock_session)
        with self.assertRaises(YandexAuthError) as ctx:
            client.get_private_resource_meta("/disk/my_folder")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_error_status_mapping_500(self):
        """HTTP 500 should be translated to YandexAPIError."""
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.request.return_value = MockResponse(500, {"message": "Internal Server Error"})

        client = YandexClient(session=mock_session)
        with self.assertRaises(YandexAPIError) as ctx:
            client.get_public_resource_meta(self.public_folder_url)
        self.assertEqual(ctx.exception.status_code, 500)

    def test_network_connection_error(self):
        """Connection errors should be wrapped in NetworkError."""
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.request.side_effect = requests.ConnectionError("Connection refused")

        client = YandexClient(session=mock_session)
        with self.assertRaises(NetworkError):
            client.get_public_resource_meta(self.public_folder_url)

    def test_private_disk_access_with_oauth(self):
        """Test private resource operations with OAuth token."""
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.request.return_value = MockResponse(200, {
            "name": "private_folder",
            "type": "dir",
            "path": "disk:/private_folder"
        })

        client = YandexClient(oauth_token="y0_AgAAAAA1234567890abcdef", session=mock_session)
        self.assertEqual(mock_session.headers.get("Authorization"), "OAuth y0_AgAAAAA1234567890abcdef")

        meta = client.get_private_resource_meta("disk:/private_folder")
        self.assertEqual(meta["name"], "private_folder")

    def test_private_disk_access_requires_token(self):
        """Test private resource operations raise YandexAuthError if no token configured."""
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        client = YandexClient(session=mock_session)

        with self.assertRaises(YandexAuthError):
            client.get_private_resource_meta("/some/path")

        with self.assertRaises(YandexAuthError):
            client.get_private_download_url("/some/path")

    def test_dpapi_encrypted_token_initialization(self):
        """Test client initialization using DPAPI encrypted token bytes."""
        from core.security import dpapi_protect

        secret_token = "y0_OAuth_Secret_Token_For_Owner"
        encrypted_bytes = dpapi_protect(secret_token.encode("utf-8"))

        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}

        client = YandexClient(dpapi_token_bytes=encrypted_bytes, session=mock_session)
        self.assertEqual(client.oauth_token, secret_token)
        self.assertEqual(mock_session.headers.get("Authorization"), f"OAuth {secret_token}")

    def test_spec_default_sources_parsing(self):
        """Verify both default URLs specified in Section 2.1 parse without error."""
        primary_folder = "https://disk.yandex.ru/d/pYDq0CSIwqclFA"
        helper_archive = "https://disk.yandex.ru/d/RgVixKr-6QlLsQ"

        self.assertEqual(clean_public_url(primary_folder), primary_folder)
        self.assertEqual(clean_public_url(helper_archive), helper_archive)

    def test_client_default_headers_and_timeout(self):
        """Verify user agent, accept headers, and timeout configuration."""
        client = YandexClient(timeout=(5.0, 15.0))
        self.assertEqual(client.timeout, (5.0, 15.0))
        self.assertEqual(client.session.headers.get("User-Agent"), DEFAULT_USER_AGENT)
        self.assertEqual(client.session.headers.get("Accept"), "application/json")


if __name__ == "__main__":
    unittest.main()
