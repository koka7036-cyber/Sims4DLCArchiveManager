"""
Network compatibility layer providing requests and urllib3 abstractions.
Uses real requests/urllib3 if installed, with a standard-library fallback
for environments where external packages are not yet populated.
Conforms to PROJECT_SPEC.md v1.1.5 section 3.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Union

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    HAS_REAL_REQUESTS = True
except ImportError:
    HAS_REAL_REQUESTS = False

    class RequestException(IOError):
        """Base request exception."""
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.response = kwargs.get("response")

    class ConnectionError(RequestException):
        """A Connection error occurred."""
        pass

    class Timeout(RequestException):
        """The request timed out."""
        pass

    class HTTPError(RequestException):
        """An HTTP error occurred."""
        pass

    class Response:
        """Lightweight Response representation compatible with requests.Response."""
        def __init__(
            self,
            status_code: int = 200,
            content: bytes = b"",
            headers: Optional[Dict[str, str]] = None,
            url: str = "",
            raw: Any = None,
        ) -> None:
            self.status_code = status_code
            self.content = content or b""
            self.headers = headers or {}
            self.url = url
            self.encoding = "utf-8"
            self.raw = raw

        @property
        def text(self) -> str:
            try:
                return self.content.decode(self.encoding or "utf-8", errors="replace")
            except Exception:
                return ""

        def json(self) -> Any:
            return json.loads(self.text)

        def iter_content(self, chunk_size: int = 1024):
            """Stream response payload in chunks."""
            if self.raw and hasattr(self.raw, "read"):
                while True:
                    chunk = self.raw.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
            elif self.content:
                for i in range(0, len(self.content), chunk_size):
                    yield self.content[i : i + chunk_size]

        def close(self) -> None:
            if self.raw and hasattr(self.raw, "close"):
                try:
                    self.raw.close()
                except Exception:
                    pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.close()

    class Retry:
        """Lightweight Retry container compatible with urllib3.util.retry.Retry."""
        def __init__(
            self,
            total: int = 3,
            connect: Optional[int] = None,
            read: Optional[int] = None,
            status: Optional[int] = None,
            backoff_factor: float = 0.5,
            status_forcelist: Optional[list[int]] = None,
            allowed_methods: Optional[list[str]] = None,
            raise_on_status: bool = False,
        ) -> None:
            self.total = total
            self.connect = connect or total
            self.read = read or total
            self.status = status or total
            self.backoff_factor = backoff_factor
            self.status_forcelist = set(status_forcelist or [])
            self.allowed_methods = set(allowed_methods or ["GET", "HEAD"])
            self.raise_on_status = raise_on_status

    class HTTPAdapter:
        """Lightweight HTTPAdapter compatible with requests.adapters.HTTPAdapter."""
        def __init__(self, max_retries: Optional[Union[Retry, int]] = None) -> None:
            self.max_retries = max_retries

    class Session:
        """Lightweight requests.Session implementation on top of urllib."""
        def __init__(self) -> None:
            self.headers: Dict[str, str] = {}
            self._adapters: Dict[str, HTTPAdapter] = {}

        def mount(self, prefix: str, adapter: HTTPAdapter) -> None:
            self._adapters[prefix] = adapter

        def request(
            self,
            method: str,
            url: str,
            params: Optional[Dict[str, Any]] = None,
            data: Any = None,
            headers: Optional[Dict[str, str]] = None,
            timeout: Optional[Union[float, tuple[float, float]]] = None,
            stream: bool = False,
        ) -> Response:
            # Build URL with params if provided
            req_url = url
            if params:
                query_string = urllib.parse.urlencode(params)
                delimiter = "&" if "?" in req_url else "?"
                req_url = f"{req_url}{delimiter}{query_string}"

            req_headers = dict(self.headers)
            if headers:
                req_headers.update(headers)

            req = urllib.request.Request(req_url, data=data, headers=req_headers, method=method.upper())

            # Determine timeout in seconds
            timeout_sec = 30.0
            if isinstance(timeout, tuple):
                timeout_sec = float(timeout[1])
            elif isinstance(timeout, (int, float)):
                timeout_sec = float(timeout)

            try:
                py_resp = urllib.request.urlopen(req, timeout=timeout_sec)
                resp_headers = {k: v for k, v in py_resp.headers.items()}
                if stream:
                    return Response(
                        status_code=py_resp.status,
                        content=b"",
                        headers=resp_headers,
                        url=req_url,
                        raw=py_resp,
                    )
                else:
                    body = py_resp.read()
                    py_resp.close()
                    return Response(
                        status_code=py_resp.status,
                        content=body,
                        headers=resp_headers,
                        url=req_url,
                    )
            except urllib.error.HTTPError as http_err:
                resp_headers = {k: v for k, v in http_err.headers.items()}
                if stream:
                    return Response(
                        status_code=http_err.code,
                        content=b"",
                        headers=resp_headers,
                        url=req_url,
                        raw=http_err,
                    )
                body = http_err.read()
                return Response(
                    status_code=http_err.code,
                    content=body,
                    headers=resp_headers,
                    url=req_url,
                )
            except urllib.error.URLError as url_err:
                raise ConnectionError(f"Connection failed: {url_err.reason}") from url_err
            except TimeoutError as timeout_err:
                raise Timeout(f"Request timed out: {timeout_err}") from timeout_err

        def get(self, url: str, **kwargs) -> Response:
            return self.request("GET", url, **kwargs)

        def post(self, url: str, **kwargs) -> Response:
            return self.request("POST", url, **kwargs)

    # Fake requests module namespace
    class _RequestsModule:
        Session = Session
        Response = Response
        RequestException = RequestException
        ConnectionError = ConnectionError
        Timeout = Timeout
        HTTPError = HTTPError

        @staticmethod
        def get(url: str, **kwargs) -> Response:
            s = Session()
            return s.get(url, **kwargs)

    requests = _RequestsModule()
