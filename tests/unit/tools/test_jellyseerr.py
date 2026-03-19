"""Tests pour framework/tools/jellyseerr.py — Gestionnaire Jellyseerr."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from urllib.error import HTTPError, URLError

import pytest
import importlib.util
import sys

# ── Load framework module directly ───────────────────────────────────────────

_FW_PATH = Path(__file__).resolve().parents[3] / "framework" / "tools" / "jellyseerr.py"


def _load_fw():
    spec = importlib.util.spec_from_file_location("fw_jellyseerr", _FW_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Enregistrer dans sys.modules AVANT exec pour que les annotations de types fonctionnent
    sys.modules["fw_jellyseerr"] = mod
    spec.loader.exec_module(mod)
    return mod


fw = _load_fw()

JellyseerrClient = fw.JellyseerrClient
MediaResult = fw.MediaResult
RequestResult = fw.RequestResult
MEDIA_STATUS = fw.MEDIA_STATUS
REQUEST_STATUS = fw.REQUEST_STATUS
cmd_health = fw.cmd_health
cmd_search = fw.cmd_search
cmd_info = fw.cmd_info
cmd_request = fw.cmd_request
cmd_status = fw.cmd_status
cmd_list = fw.cmd_list
cmd_cancel = fw.cmd_cancel
cmd_report = fw.cmd_report
_validate_url = fw._validate_url
_parse_media_result = fw._parse_media_result
main = fw.main


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_response(data: dict | list, status: int = 200):
    """Crée un mock urllib response."""
    body = json.dumps(data).encode()
    mock = MagicMock()
    mock.read.return_value = body
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    mock.status = status
    return mock


def _make_http_error(code: int, msg: str = "Error", body: dict | None = None) -> HTTPError:
    """Crée un HTTPError simulé."""
    body_bytes = json.dumps(body or {"message": msg}).encode()
    fp = BytesIO(body_bytes)
    fp.read = lambda: body_bytes  # type: ignore
    return HTTPError(url="http://test", code=code, msg=msg, hdrs=None, fp=fp)


def _movie_raw(
    tmdb_id: int = 27205,
    title: str = "Inception",
    year: str = "2010-07-16",
    status: int = 1,
    requests: list | None = None,
) -> dict:
    return {
        "id": tmdb_id,
        "mediaType": "movie",
        "title": title,
        "releaseDate": year,
        "overview": "A thief who steals corporate secrets...",
        "originalLanguage": "en",
        "genres": [{"id": 28, "name": "Action"}, {"id": 878, "name": "Science Fiction"}],
        "mediaInfo": {
            "status": status,
            "requests": requests or [],
        },
    }


def _tv_raw(
    tmdb_id: int = 1396,
    name: str = "Breaking Bad",
    year: str = "2008-01-20",
    status: int = 1,
    requests: list | None = None,
) -> dict:
    return {
        "id": tmdb_id,
        "mediaType": "tv",
        "name": name,
        "firstAirDate": year,
        "overview": "A high school chemistry teacher...",
        "originalLanguage": "en",
        "genres": [{"id": 18, "name": "Drama"}],
        "mediaInfo": {
            "status": status,
            "requests": requests or [],
        },
    }


def _request_raw(
    req_id: int = 42,
    status: int = 2,
    tmdb_id: int = 27205,
    media_type: str = "movie",
    title: str = "Inception",
    created_at: str = "2025-01-01T00:00:00.000Z",
) -> dict:
    return {
        "id": req_id,
        "status": status,
        "createdAt": created_at,
        "updatedAt": created_at,
        "media": {
            "mediaType": media_type,
            "tmdbId": tmdb_id,
            "title": title if media_type == "movie" else None,
            "name": title if media_type == "tv" else None,
            "status": 3,  # PROCESSING
        },
        "requestedBy": {"displayName": "Guilhem"},
    }


# ── Tests: SSRF Protection ────────────────────────────────────────────────────

class TestSSRFProtection:
    def test_blocks_cloud_metadata_ip(self):
        with pytest.raises(ValueError, match="cloud metadata"):
            _validate_url("http://169.254.169.254/latest/meta-data/")

    def test_blocks_google_metadata(self):
        with pytest.raises(ValueError, match="cloud metadata"):
            _validate_url("http://metadata.google.internal/")

    def test_rejects_non_http_scheme(self):
        with pytest.raises(ValueError, match="schéma"):
            _validate_url("ftp://example.com")

    def test_accepts_http(self):
        assert _validate_url("http://localhost:5055") == "http://localhost:5055"

    def test_accepts_https(self):
        assert _validate_url("https://g-medias.srvdreamer.fr") == "https://g-medias.srvdreamer.fr"

    def test_client_validates_url_on_init(self):
        with pytest.raises(ValueError, match="cloud metadata"):
            JellyseerrClient("http://169.254.169.254")


# ── Tests: JellyseerrClient ───────────────────────────────────────────────────

class TestJellyseerrClient:
    def test_headers_without_api_key(self):
        client = JellyseerrClient("http://localhost:5055")
        headers = client._headers()
        assert "X-Api-Key" not in headers
        assert headers["Content-Type"] == "application/json"

    def test_headers_with_api_key(self):
        client = JellyseerrClient("http://localhost:5055", api_key="secret123")
        headers = client._headers()
        assert headers["X-Api-Key"] == "secret123"

    def test_base_url_trailing_slash_stripped(self):
        client = JellyseerrClient("http://localhost:5055/")
        assert client._base == "http://localhost:5055"

    @patch("fw_jellyseerr.urlopen")
    def test_get_status_success(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({"version": "3.1.0"})
        client = JellyseerrClient("http://localhost:5055")
        result = client.get_status()
        assert result["version"] == "3.1.0"

    @patch("fw_jellyseerr.urlopen")
    def test_get_status_connection_error(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("Connection refused")
        client = JellyseerrClient("http://localhost:5055")
        with pytest.raises(URLError):
            client.get_status()

    @patch("fw_jellyseerr.urlopen")
    def test_get_status_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = _make_http_error(401, "Unauthorized")
        client = JellyseerrClient("http://localhost:5055")
        with pytest.raises(HTTPError) as exc_info:
            client.get_status()
        assert exc_info.value.code == 401

    @patch("fw_jellyseerr.urlopen")
    def test_search_encodes_query(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({"results": [], "totalResults": 0})
        client = JellyseerrClient("http://localhost:5055")
        client.search("The Dark Knight")
        call_args = mock_urlopen.call_args
        url = call_args[0][0].full_url
        assert "The+Dark+Knight" in url or "The%20Dark%20Knight" in url

    def test_create_request_requires_api_key(self):
        client = JellyseerrClient("http://localhost:5055")  # no api_key
        with pytest.raises(ValueError, match="API key requise"):
            client.create_request("movie", 27205)

    def test_delete_request_requires_api_key(self):
        client = JellyseerrClient("http://localhost:5055")
        with pytest.raises(ValueError, match="API key requise"):
            client.delete_request(42)

    @patch("fw_jellyseerr.urlopen")
    def test_empty_response_returns_empty_dict(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp
        client = JellyseerrClient("http://localhost:5055")
        result = client.get_status()
        assert result == {}


# ── Tests: MediaResult parsing ────────────────────────────────────────────────

class TestMediaResultParsing:
    def test_parse_movie(self):
        m = _parse_media_result(_movie_raw(status=5))
        assert m.media_type == "movie"
        assert m.media_id == 27205
        assert m.title == "Inception"
        assert m.year == 2010
        assert m.is_available is True
        assert "Action" in m.genres

    def test_parse_tv_show(self):
        m = _parse_media_result(_tv_raw(name="Breaking Bad", status=1))
        assert m.media_type == "tv"
        assert m.title == "Breaking Bad"
        assert m.year == 2008
        assert m.is_available is False
        assert m.availability_label == "UNKNOWN"

    def test_available_status(self):
        m = _parse_media_result(_movie_raw(status=5))
        assert m.is_available is True
        assert m.is_partially_available is False
        assert m.is_pending_or_processing is False

    def test_partially_available_status(self):
        m = _parse_media_result(_movie_raw(status=4))
        assert m.is_available is False
        assert m.is_partially_available is True

    def test_processing_status(self):
        m = _parse_media_result(_movie_raw(status=3))
        assert m.is_pending_or_processing is True

    def test_pending_status(self):
        m = _parse_media_result(_movie_raw(status=2))
        assert m.is_pending_or_processing is True

    def test_has_pending_request(self):
        existing = [{"id": 1, "status": 2}]  # APPROVED
        m = _parse_media_result(_movie_raw(requests=existing))
        assert m.has_pending_request is True

    def test_no_pending_request_when_declined(self):
        existing = [{"id": 1, "status": 3}]  # DECLINED
        m = _parse_media_result(_movie_raw(requests=existing))
        assert m.has_pending_request is False

    def test_empty_media_info(self):
        raw = {"id": 100, "mediaType": "movie", "title": "Test"}
        m = _parse_media_result(raw)
        assert m.availability_status == 1
        assert m.has_pending_request is False

    def test_missing_year(self):
        raw = {"id": 100, "mediaType": "movie", "title": "Test", "releaseDate": ""}
        m = _parse_media_result(raw)
        assert m.year is None

    def test_summary_line_format(self):
        m = _parse_media_result(_movie_raw(status=5))
        line = m.summary_line()
        assert "Inception" in line
        assert "2010" in line
        assert "✅" in line


# ── Tests: cmd_health ─────────────────────────────────────────────────────────

class TestCmdHealth:
    @patch("fw_jellyseerr.urlopen")
    def test_health_ok(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({"version": "3.1.0"})
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_health(client)
        assert result["ok"] is True
        assert result["version"] == "3.1.0"
        assert "latency_ms" in result

    @patch("fw_jellyseerr.urlopen")
    def test_health_connection_refused(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("Connection refused")
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_health(client)
        assert result["ok"] is False
        assert result["error_type"] == "connection"

    @patch("fw_jellyseerr.urlopen")
    def test_health_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = _make_http_error(500, "Internal Server Error")
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_health(client)
        assert result["ok"] is False
        assert result["error_type"] == "http"
        assert result["code"] == 500


# ── Tests: cmd_search ─────────────────────────────────────────────────────────

class TestCmdSearch:
    @patch("fw_jellyseerr.urlopen")
    def test_search_returns_results(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({
            "results": [_movie_raw(status=1), _movie_raw(tmdb_id=550, title="Fight Club")],
            "totalResults": 2,
        })
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_search(client, "Inception")
        assert result["ok"] is True
        assert result["count"] == 2
        assert result["results"][0]["title"] == "Inception"

    @patch("fw_jellyseerr.urlopen")
    def test_search_empty_results(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({"results": [], "totalResults": 0})
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_search(client, "xyznofilm")
        assert result["ok"] is True
        assert result["count"] == 0

    @patch("fw_jellyseerr.urlopen")
    def test_search_filters_persons(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({
            "results": [
                _movie_raw(status=1),
                {"mediaType": "person", "name": "Christopher Nolan"},
            ],
            "totalResults": 2,
        })
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_search(client, "Nolan")
        # Les personnes doivent être filtrées
        assert result["count"] == 1
        assert result["results"][0]["media_type"] == "movie"

    @patch("fw_jellyseerr.urlopen")
    def test_search_filters_by_type(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({
            "results": [_movie_raw(status=1), _tv_raw(status=1)],
            "totalResults": 2,
        })
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_search(client, "test", media_type="movie")
        assert all(r["media_type"] == "movie" for r in result["results"])

    @patch("fw_jellyseerr.urlopen")
    def test_search_includes_availability(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({
            "results": [_movie_raw(status=5)],
            "totalResults": 1,
        })
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_search(client, "Inception")
        assert result["results"][0]["is_available"] is True
        assert result["results"][0]["availability_label"] == "AVAILABLE"

    @patch("fw_jellyseerr.urlopen")
    def test_search_connection_error(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("timeout")
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_search(client, "test")
        assert result["ok"] is False
        assert result["results"] == []


# ── Tests: cmd_info ───────────────────────────────────────────────────────────

class TestCmdInfo:
    @patch("fw_jellyseerr.urlopen")
    def test_info_movie(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_movie_raw(status=5))
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_info(client, "movie", 27205)
        assert result["ok"] is True
        assert result["title"] == "Inception"
        assert result["is_available"] is True

    @patch("fw_jellyseerr.urlopen")
    def test_info_tv_show(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_tv_raw(status=3))
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_info(client, "tv", 1396)
        assert result["ok"] is True
        assert result["title"] == "Breaking Bad"
        assert result["is_pending_or_processing"] is True

    def test_info_invalid_media_type(self):
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_info(client, "anime", 123)
        assert result["ok"] is False
        assert "invalide" in result["error"]

    @patch("fw_jellyseerr.urlopen")
    def test_info_404_not_found(self, mock_urlopen):
        mock_urlopen.side_effect = _make_http_error(404, "Not Found")
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_info(client, "movie", 99999)
        assert result["ok"] is False
        assert result["code"] == 404

    @patch("fw_jellyseerr.urlopen")
    def test_info_shows_existing_requests(self, mock_urlopen):
        existing = [{"id": 42, "status": 2, "createdAt": "2025-01-01T00:00:00Z",
                     "requestedBy": {"displayName": "Guilhem"}}]
        mock_urlopen.return_value = _mock_response(_movie_raw(status=3, requests=existing))
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_info(client, "movie", 27205)
        assert result["has_pending_request"] is True
        assert len(result["existing_requests"]) == 1
        assert result["existing_requests"][0]["request_id"] == 42


# ── Tests: cmd_request (Anti-False-Positive Pipeline) ────────────────────────

class TestCmdRequest:
    @patch("fw_jellyseerr.urlopen")
    def test_request_invalid_media_type(self, mock_urlopen):
        client = JellyseerrClient("http://localhost:5055", api_key="key")
        result = cmd_request(client, "anime", 123)
        assert result["success"] is False
        assert "invalide" in result["reason"]
        mock_urlopen.assert_not_called()

    @patch("fw_jellyseerr.urlopen")
    def test_request_skipped_when_already_available(self, mock_urlopen):
        # health + info (available)
        mock_urlopen.side_effect = [
            _mock_response({"version": "3.1.0"}),  # health
            _mock_response(_movie_raw(status=5)),   # info (AVAILABLE)
        ]
        client = JellyseerrClient("http://localhost:5055", api_key="key")
        result = cmd_request(client, "movie", 27205)
        assert result["success"] is True
        assert result["action"] == "skipped"
        assert "Déjà disponible" in result["reason"]

    @patch("fw_jellyseerr.urlopen")
    def test_request_skipped_when_already_requested(self, mock_urlopen):
        existing_req = [{"id": 10, "status": 2, "createdAt": "2025-01-01T00:00:00Z",
                         "requestedBy": {"displayName": "Guilhem"}}]
        mock_urlopen.side_effect = [
            _mock_response({"version": "3.1.0"}),                    # health
            _mock_response(_movie_raw(status=1, requests=existing_req)),  # info
        ]
        client = JellyseerrClient("http://localhost:5055", api_key="key")
        result = cmd_request(client, "movie", 27205)
        assert result["success"] is True
        assert result["action"] == "skipped"
        assert "en cours" in result["reason"]

    @patch("fw_jellyseerr.urlopen")
    def test_request_created_successfully(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_response({"version": "3.1.0"}),    # health
            _mock_response(_movie_raw(status=1)),     # info (UNKNOWN)
            _mock_response({"id": 99, "status": 1}), # POST request
        ]
        client = JellyseerrClient("http://localhost:5055", api_key="key")
        result = cmd_request(client, "movie", 27205)
        assert result["success"] is True
        assert result["action"] == "created"
        assert result["request_id"] == 99

    @patch("fw_jellyseerr.urlopen")
    def test_request_force_bypasses_available_check(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_response({"version": "3.1.0"}),    # health
            _mock_response(_movie_raw(status=5)),     # info (AVAILABLE)
            _mock_response({"id": 88, "status": 1}), # POST request
        ]
        client = JellyseerrClient("http://localhost:5055", api_key="key")
        result = cmd_request(client, "movie", 27205, force=True)
        assert result["success"] is True
        assert result["action"] == "created"

    @patch("fw_jellyseerr.urlopen")
    def test_request_health_check_fails(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("Connection refused")
        client = JellyseerrClient("http://localhost:5055", api_key="key")
        result = cmd_request(client, "movie", 27205)
        assert result["success"] is False
        assert result["action"] == "error"
        assert "injoignable" in result["reason"]

    @patch("fw_jellyseerr.urlopen")
    def test_request_media_not_found(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_response({"version": "3.1.0"}),  # health
            _make_http_error(404, "Not Found"),     # info → 404
        ]
        client = JellyseerrClient("http://localhost:5055", api_key="key")
        result = cmd_request(client, "movie", 99999)
        assert result["success"] is False
        assert result["action"] == "error"

    @patch("fw_jellyseerr.urlopen")
    def test_request_api_error_on_submit(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_response({"version": "3.1.0"}),  # health
            _mock_response(_movie_raw(status=1)),   # info
            _make_http_error(422, "Unprocessable"),  # POST fails
        ]
        client = JellyseerrClient("http://localhost:5055", api_key="key")
        result = cmd_request(client, "movie", 27205)
        assert result["success"] is False
        assert result["action"] == "error"

    @patch("fw_jellyseerr.urlopen")
    def test_request_no_api_key_fails(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_response({"version": "3.1.0"}),  # health
            _mock_response(_movie_raw(status=1)),   # info
        ]
        client = JellyseerrClient("http://localhost:5055")  # no api_key
        result = cmd_request(client, "movie", 27205)
        assert result["success"] is False
        assert result["action"] == "error"
        assert "API key" in result["reason"]

    @patch("fw_jellyseerr.urlopen")
    def test_request_tv_with_seasons(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_response({"version": "3.1.0"}),    # health
            _mock_response(_tv_raw(status=1)),         # info
            _mock_response({"id": 77, "status": 1}),  # POST
        ]
        client = JellyseerrClient("http://localhost:5055", api_key="key")
        result = cmd_request(client, "tv", 1396, seasons=[1, 2])
        assert result["success"] is True
        assert result["action"] == "created"

    @patch("fw_jellyseerr.urlopen")
    def test_request_declined_existing_does_not_block(self, mock_urlopen):
        existing_req = [{"id": 10, "status": 3}]  # DECLINED → non bloquant
        mock_urlopen.side_effect = [
            _mock_response({"version": "3.1.0"}),
            _mock_response(_movie_raw(status=1, requests=existing_req)),
            _mock_response({"id": 55, "status": 1}),
        ]
        client = JellyseerrClient("http://localhost:5055", api_key="key")
        result = cmd_request(client, "movie", 27205)
        assert result["success"] is True
        assert result["action"] == "created"


# ── Tests: cmd_status ─────────────────────────────────────────────────────────

class TestCmdStatus:
    @patch("fw_jellyseerr.urlopen")
    def test_status_approved(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_request_raw(req_id=42, status=2))
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_status(client, 42)
        assert result["ok"] is True
        assert result["request_id"] == 42
        assert result["status"] == "APPROVED"
        assert result["title"] == "Inception"

    @patch("fw_jellyseerr.urlopen")
    def test_status_pending_approval(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_request_raw(req_id=7, status=1))
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_status(client, 7)
        assert result["status"] == "PENDING_APPROVAL"

    @patch("fw_jellyseerr.urlopen")
    def test_status_not_found(self, mock_urlopen):
        mock_urlopen.side_effect = _make_http_error(404, "Not Found")
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_status(client, 9999)
        assert result["ok"] is False
        assert "introuvable" in result["error"]


# ── Tests: cmd_list ───────────────────────────────────────────────────────────

class TestCmdList:
    @patch("fw_jellyseerr.urlopen")
    def test_list_all(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({
            "results": [_request_raw(req_id=1), _request_raw(req_id=2, status=1)],
            "pageInfo": {"results": 2},
        })
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_list(client)
        assert result["ok"] is True
        assert result["count"] == 2
        assert len(result["requests"]) == 2

    @patch("fw_jellyseerr.urlopen")
    def test_list_empty(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({"results": [], "pageInfo": {"results": 0}})
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_list(client)
        assert result["ok"] is True
        assert result["count"] == 0

    def test_list_invalid_filter(self):
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_list(client, filter_status="invalid_filter")
        assert result["ok"] is False
        assert "Filtre invalide" in result["error"]

    @patch("fw_jellyseerr.urlopen")
    def test_list_request_fields(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({
            "results": [_request_raw(req_id=5, status=2)],
            "pageInfo": {"results": 1},
        })
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_list(client)
        req = result["requests"][0]
        assert req["request_id"] == 5
        assert req["status"] == "APPROVED"
        assert req["title"] == "Inception"
        assert req["media_type"] == "movie"


# ── Tests: cmd_cancel ─────────────────────────────────────────────────────────

class TestCmdCancel:
    @patch("fw_jellyseerr.urlopen")
    def test_cancel_pending_request(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_response(_request_raw(req_id=42, status=1)),  # get_request
            _mock_response({}),                                   # delete
        ]
        client = JellyseerrClient("http://localhost:5055", api_key="key")
        result = cmd_cancel(client, 42)
        assert result["ok"] is True
        assert result["action"] == "cancelled"
        assert result["request_id"] == 42

    @patch("fw_jellyseerr.urlopen")
    def test_cancel_already_declined_is_noop(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_request_raw(req_id=10, status=3))
        client = JellyseerrClient("http://localhost:5055", api_key="key")
        result = cmd_cancel(client, 10)
        assert result["ok"] is True
        assert result["action"] == "noop"

    @patch("fw_jellyseerr.urlopen")
    def test_cancel_not_found(self, mock_urlopen):
        mock_urlopen.side_effect = _make_http_error(404, "Not Found")
        client = JellyseerrClient("http://localhost:5055", api_key="key")
        result = cmd_cancel(client, 9999)
        assert result["ok"] is False


# ── Tests: cmd_report ─────────────────────────────────────────────────────────

class TestCmdReport:
    @patch("fw_jellyseerr.urlopen")
    def test_report_when_healthy(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_response({"version": "3.1.0"}),  # health
            _mock_response({"results": [_request_raw()], "pageInfo": {"results": 1}}),  # stats
        ]
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_report(client)
        assert result["health"]["ok"] is True
        assert result["stats"]["total"] == 1

    @patch("fw_jellyseerr.urlopen")
    def test_report_when_unhealthy(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("Connection refused")
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_report(client)
        assert result["health"]["ok"] is False


# ── Tests: CLI (main) ─────────────────────────────────────────────────────────

class TestCLI:
    @patch("fw_jellyseerr.urlopen")
    def test_main_health(self, mock_urlopen, capsys):
        mock_urlopen.return_value = _mock_response({"version": "3.1.0"})
        rc = main(["--url", "http://localhost:5055", "--json", "health"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["ok"] is True
        assert rc == 0

    @patch("fw_jellyseerr.urlopen")
    def test_main_health_failure_returns_nonzero(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("refused")
        rc = main(["--url", "http://localhost:5055", "health"])
        assert rc == 1

    def test_main_ssrf_blocked(self, capsys):
        rc = main(["--url", "http://169.254.169.254", "health"])
        assert rc == 1

    @patch("fw_jellyseerr.urlopen")
    def test_main_search_outputs_json(self, mock_urlopen, capsys):
        mock_urlopen.return_value = _mock_response({
            "results": [_movie_raw(status=1)],
            "totalResults": 1,
        })
        rc = main(["--url", "http://localhost:5055", "search", "Inception"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["count"] == 1
        assert rc == 0

    @patch("fw_jellyseerr.urlopen")
    def test_main_list_outputs_json(self, mock_urlopen, capsys):
        mock_urlopen.return_value = _mock_response({
            "results": [],
            "pageInfo": {"results": 0},
        })
        rc = main(["--url", "http://localhost:5055", "list"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["ok"] is True
        assert rc == 0

    @patch("fw_jellyseerr.urlopen")
    def test_main_quiet_suppresses_stderr(self, mock_urlopen, capsys):
        mock_urlopen.return_value = _mock_response({"version": "3.1.0"})
        main(["--url", "http://localhost:5055", "--quiet", "--json", "health"])
        err = capsys.readouterr().err
        assert err == ""

    @patch("fw_jellyseerr.urlopen")
    def test_main_info_json(self, mock_urlopen, capsys):
        mock_urlopen.return_value = _mock_response(_movie_raw(status=5))
        rc = main([
            "--url", "http://localhost:5055",
            "info", "--media-type", "movie", "--media-id", "27205",
        ])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["ok"] is True
        assert data["is_available"] is True
        assert rc == 0


# ── Tests: Constants & Status Mappings ───────────────────────────────────────

class TestConstants:
    def test_all_media_statuses_defined(self):
        for i in range(1, 6):
            assert i in MEDIA_STATUS

    def test_all_request_statuses_defined(self):
        for i in range(1, 4):
            assert i in REQUEST_STATUS

    def test_media_status_5_is_available(self):
        assert MEDIA_STATUS[5] == "AVAILABLE"

    def test_request_status_2_is_approved(self):
        assert REQUEST_STATUS[2] == "APPROVED"

    def test_request_status_3_is_declined(self):
        assert REQUEST_STATUS[3] == "DECLINED"


# ── Tests: Edge cases ─────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_parse_media_result_unknown_genre_format(self):
        raw = {
            "id": 100, "mediaType": "movie", "title": "Test",
            "genres": ["Action", "Drama"],  # chaînes au lieu de dicts
        }
        m = _parse_media_result(raw)
        assert "Action" in m.genres

    def test_parse_media_result_null_media_info(self):
        raw = {"id": 100, "mediaType": "movie", "title": "Test", "mediaInfo": None}
        m = _parse_media_result(raw)
        assert m.availability_status == 1
        assert m.has_pending_request is False

    @patch("fw_jellyseerr.urlopen")
    def test_cmd_request_partially_available_proceeds(self, mock_urlopen):
        """PARTIALLY_AVAILABLE ne bloque pas — peut demander les saisons manquantes."""
        mock_urlopen.side_effect = [
            _mock_response({"version": "3.1.0"}),    # health
            _mock_response(_movie_raw(status=4)),     # info (PARTIALLY_AVAILABLE)
            _mock_response({"id": 55, "status": 1}), # POST
        ]
        client = JellyseerrClient("http://localhost:5055", api_key="key")
        result = cmd_request(client, "movie", 27205)
        # PARTIALLY_AVAILABLE n'est pas bloquant (seul AVAILABLE l'est)
        assert result["success"] is True

    @patch("fw_jellyseerr.urlopen")
    def test_search_returns_overview_truncated(self, mock_urlopen):
        long_overview = "A" * 1000
        raw = _movie_raw()
        raw["overview"] = long_overview
        mock_urlopen.return_value = _mock_response({"results": [raw], "totalResults": 1})
        client = JellyseerrClient("http://localhost:5055")
        result = cmd_search(client, "test")
        assert len(result["results"][0]["overview"]) <= 200

    def test_validate_url_strips_nothing(self):
        url = "http://192.168.2.71:30055"
        assert _validate_url(url) == url

    def test_validate_url_accepts_ip(self):
        assert _validate_url("http://192.168.2.71:5055") == "http://192.168.2.71:5055"

    def test_validate_url_accepts_domain(self):
        assert _validate_url("https://requests.srvdreamer.fr") == "https://requests.srvdreamer.fr"
