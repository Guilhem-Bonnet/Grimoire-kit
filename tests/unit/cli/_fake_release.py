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


class FakeGithub(FakeRelease):
    """``FakeRelease`` (PyPI : une version stable) + les releases GitHub avec une pré-version.

    Une pré-version n'existe que sur GitHub (``publish.yml`` ne l'envoie pas à PyPI) : tag
    ``v3.64.0-rc1``, wheel nommée ``3.64.0rc1`` (PEP 440), ``SHA256SUMS`` à côté.
    """

    API = "https://api.github.com/repos/Guilhem-Bonnet/Grimoire-kit/releases?per_page=30"

    def __init__(
        self,
        *,
        stable: str = "3.62.0",
        tag: str = "v3.64.0-rc1",
        pre_wheel: bytes = b"rc-wheel",
        pre_served: bytes | None = None,
        pre_sums: str | int | None = "auto",
        prerelease: bool = True,
        draft: bool = False,
        asset_url: str | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(version=stable, **kw)
        self.tag = tag
        number = tag.removeprefix("v").replace("-rc", "rc")
        self.rc_name = f"grimoire_kit-{number}-py3-none-any.whl"
        self.rc_url = asset_url or f"https://github.com/Guilhem-Bonnet/Grimoire-kit/releases/download/{tag}/{self.rc_name}"
        self.rc_served = pre_wheel if pre_served is None else pre_served
        self.rc_sums = f"{sha(pre_wheel)}  {self.rc_name}\n" if pre_sums == "auto" else pre_sums
        self.prerelease, self.draft = prerelease, draft

    def listing(self) -> list[dict[str, Any]]:
        asset = {"name": self.rc_name, "browser_download_url": self.rc_url}
        return [{"tag_name": self.tag, "draft": self.draft, "prerelease": self.prerelease, "assets": [asset]}]

    def __call__(self, url: str, timeout: float | None = None) -> _Response:
        if url == self.API:
            self.requested.append(url)
            return _Response(json.dumps(self.listing()).encode())
        if url == self.rc_url:
            self.requested.append(url)
            return _Response(self.rc_served)
        if url == f"https://github.com/Guilhem-Bonnet/Grimoire-kit/releases/download/{self.tag}/SHA256SUMS":
            self.requested.append(url)
            if self.rc_sums is None:
                raise HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
            if isinstance(self.rc_sums, int):
                raise HTTPError(url, self.rc_sums, "Server Error", {}, None)  # type: ignore[arg-type]
            return _Response(self.rc_sums.encode())
        return super().__call__(url, timeout)
