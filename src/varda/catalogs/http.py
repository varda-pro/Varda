"""JSON over HTTPS for catalogs (with certifi's certificates, as the bundled
app can't rely on the system's)."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

import certifi

from varda.catalogs.catalog import CatalogError
from varda.version import vardaVersion

TIMEOUT_S = 60


def _headers(extra: Mapping[str, str] | None) -> dict[str, str]:
    return {
        "User-Agent": f"Varda/{vardaVersion()} (https://github.com/varda-pro/Varda)",
        "Accept": "application/json, application/geo+json",
        **dict(extra or {}),
    }


def fetchJson(
    url: str,
    params: Mapping[str, object] | None = None,
    body: object | None = None,
    headers: Mapping[str, str] | None = None,
) -> dict:
    """GET (or POST ``body`` as JSON) and decode the reply. Raises
    CatalogError with a message for the user."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}
        )
    data = json.dumps(body).encode() if body is not None else None
    allHeaders = _headers(headers)
    if data is not None:
        allHeaders["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=allHeaders)
    context = ssl.create_default_context(cafile=certifi.where())
    host = urllib.parse.urlparse(url).netloc
    try:
        with urllib.request.urlopen(
            request,
            timeout=TIMEOUT_S,
            context=context if url.startswith("https") else None,
        ) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        raise CatalogError(f"{host} returned an error ({error.code}).") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise CatalogError(
            f"{host} couldn't be reached; check your internet connection."
        ) from error
    except ValueError as error:
        raise CatalogError(f"{host} sent a reply Varda couldn't read.") from error


def fetchBytes(
    url: str,
    headers: Mapping[str, str] | None = None,
    limit: int = 5_000_000,
    timeout: float = TIMEOUT_S,
) -> bytes:
    """A small file (a thumbnail, say) in memory; at most ``limit`` bytes,
    giving up after ``timeout`` seconds without a reply."""
    request = urllib.request.Request(
        url, headers={**_headers(headers), "Accept": "*/*"}
    )
    context = ssl.create_default_context(cafile=certifi.where())
    host = urllib.parse.urlparse(url).netloc
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=context if url.startswith("https") else None,
        ) as response:
            return response.read(limit)
    except urllib.error.HTTPError as error:
        raise CatalogError(f"{host} returned an error ({error.code}).") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise CatalogError(f"{host} couldn't be reached.") from error
