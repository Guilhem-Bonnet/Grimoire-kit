"""Une release fictive (PyPI, fichiers, SHA256SUMS) servie par un faux ``urlopen``."""

from __future__ import annotations

import hashlib
import io
import json
from typing import Any
from urllib.error import HTTPError

FILES_HOST = "https://files.pythonhosted.org/packages/ab/cd"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakeRelease:
    """``urlopen`` factice : PyPI, le fichier de wheel et le ``SHA256SUMS`` de la release."""

    def __init__(
        self,
        version: str = "99.0.0",
        *,
        wheel: bytes = b"wheel-bytes",
        served_wheel: bytes | None = None,
        sums: str | int | None = "auto",
        pypi_sha: str | None = "auto",
    ) -> None:
        self.version = version
        self.filename = f"grimoire_kit-{version}-py3-none-any.whl"
        self.url = f"{FILES_HOST}/{self.filename}"
        self.published = wheel
        self.served = wheel if served_wheel is None else served_wheel
        self.pypi_sha = sha(wheel) if pypi_sha == "auto" else pypi_sha
        self.sums = f"{sha(wheel)}  {self.filename}\n" if sums == "auto" else sums
        self.requested: list[str] = []

    def pypi_payload(self) -> dict[str, Any]:
        digests = {"sha256": self.pypi_sha} if self.pypi_sha else {}
        entry = {
            "packagetype": "bdist_wheel", "filename": self.filename, "url": self.url,
            "digests": digests, "yanked": False,
        }
        return {"info": {"version": self.version}, "releases": {self.version: [entry]}, "urls": [entry]}

    def __call__(self, url: str, timeout: float | None = None) -> _Response:
        self.requested.append(url)
        if url.startswith("https://pypi.org/pypi/"):
            return _Response(json.dumps(self.pypi_payload()).encode())
        if url == self.url:
            return _Response(self.served)
        if url.endswith("/SHA256SUMS"):
            if self.sums is None:
                raise HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
            if isinstance(self.sums, int):
                raise HTTPError(url, self.sums, "Server Error", {}, None)  # type: ignore[arg-type]
            return _Response(self.sums.encode())
        raise AssertionError(f"URL inattendue : {url}")
