"""Export OTLP/HTTP (JSON) des traces vers un collecteur réel (W4-05).

Le JSONL OTel (``TraceLedger.export_otel_jsonl``) reste la forme de référence
(``grimoire.otel.v2``) ; ce module n'en est qu'une projection au format du fil
OTLP/JSON, jamais une seconde vérité : mêmes spans, mêmes attributs, donc un
collecteur et un fichier ne divergent pas.

Un échec se voit. Un statut HTTP hors 2xx, un ``partialSuccess`` qui rejette
des spans, un collecteur injoignable ou un schéma d'URL autre que http(s)
lèvent :class:`OtlpExportError` — l'export ne rend un nombre de spans que
s'ils ont été acceptés. Pas de relance automatique : l'appelant décide.

Une commande explicite l'appelle (``grimoire task trace-export --format
otlp``) ; rien n'émet vers le réseau de lui-même.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from grimoire.core.exceptions import GrimoireRuntimeError

if TYPE_CHECKING:
    from grimoire.traces.ledger import TraceLedger

__all__ = ["OtlpExportError", "build_otlp_requests", "export_otlp"]

DEFAULT_BATCH_SIZE = 500
DEFAULT_TIMEOUT_S = 10.0
_TRACES_PATH = "/v1/traces"
_SCOPE_NAME = "grimoire.traces"
_KIND = {"SPAN_KIND_INTERNAL": 1}
_STATUS = {"STATUS_CODE_OK": 1, "STATUS_CODE_ERROR": 2}


class OtlpExportError(GrimoireRuntimeError):
    """Le collecteur n'a pas accepté le lot (ou n'a pas pu être joint)."""


def _any_value(value: Any) -> dict[str, Any] | None:
    # bool avant int : bool est un int en Python.
    if value is None:
        return None
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def _key_values(attrs: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Attributs OTLP ; une valeur ``None`` est omise (inconnue), jamais écrite en zéro."""
    out: list[dict[str, Any]] = []
    for key, value in attrs.items():
        encoded = _any_value(value)
        if encoded is not None:
            out.append({"key": key, "value": encoded})
    return out


def _span_to_otlp(span: Mapping[str, Any]) -> dict[str, Any]:
    start = int(span.get("startTimeUnixNano", 0))
    end = int(span.get("endTimeUnixNano", 0))
    attrs = dict(span.get("attributes", {}))
    if end < start:
        # Run non terminé : OTLP interdit une fin avant le début. On le dit au lieu d'inventer une durée.
        end = start
        attrs["grimoire.end_time_missing"] = True
    out: dict[str, Any] = {
        "traceId": span["traceId"],
        "spanId": span["spanId"],
        "name": span["name"],
        "kind": _KIND.get(str(span.get("kind")), 1),
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(end),
        "attributes": _key_values(attrs),
        "status": {"code": _STATUS.get(str(span.get("status", {}).get("code")), 0)},
    }
    if span.get("parentSpanId"):
        out["parentSpanId"] = span["parentSpanId"]
    return out


def build_otlp_requests(spans: list[dict[str, Any]], *, batch_size: int = DEFAULT_BATCH_SIZE) -> list[dict[str, Any]]:
    """Corps ``ExportTraceServiceRequest`` (JSON) à partir des spans ``grimoire.otel.v2``.

    Les spans sont regroupés par ressource (l'agent et l'hôte changent la
    ressource) puis découpés en lots d'au plus *batch_size* spans.
    """
    size = max(1, batch_size)
    requests: list[dict[str, Any]] = []
    for start in range(0, len(spans), size):
        chunk = spans[start : start + size]
        by_resource: dict[str, list[dict[str, Any]]] = defaultdict(list)
        resources: dict[str, Mapping[str, Any]] = {}
        for span in chunk:
            resource = span.get("resource", {}).get("attributes", {})
            key = json.dumps(resource, sort_keys=True, default=str)
            resources[key] = resource
            by_resource[key].append(_span_to_otlp(span))
        requests.append(
            {
                "resourceSpans": [
                    {
                        "resource": {"attributes": _key_values(resources[key])},
                        "scopeSpans": [{"scope": {"name": _SCOPE_NAME}, "spans": grouped}],
                    }
                    for key, grouped in by_resource.items()
                ]
            }
        )
    return requests


def _traces_url(endpoint: str) -> str:
    parts = urlparse.urlsplit(endpoint)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise OtlpExportError(f"Endpoint OTLP invalide : {endpoint!r} (attendu : une URL http ou https).")
    if parts.path in {"", "/"}:
        parts = parts._replace(path=_TRACES_PATH)
    return urlparse.urlunsplit(parts)


def _post(url: str, body: dict[str, Any], headers: Mapping[str, str], timeout: float) -> None:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urlrequest.Request(url, data=data, method="POST")  # noqa: S310 — schéma validé par _traces_url
    request.add_header("Content-Type", "application/json")
    for name, value in headers.items():
        request.add_header(name, value)
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(response.status)
            raw = response.read()
    except urlerror.HTTPError as exc:
        raise OtlpExportError(f"Le collecteur {url} a refusé le lot : HTTP {exc.code}.") from exc
    except (urlerror.URLError, OSError, TimeoutError) as exc:
        raise OtlpExportError(f"Collecteur OTLP injoignable ({url}) : {exc}.") from exc
    if not 200 <= status < 300:
        raise OtlpExportError(f"Le collecteur {url} a répondu HTTP {status}.")
    _check_partial_success(url, raw)


def _check_partial_success(url: str, raw: bytes) -> None:
    try:
        reply = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, ValueError):
        return  # silent-ok — un 2xx sans corps JSON : le collecteur a accepté, rien à lire
    partial = reply.get("partialSuccess") if isinstance(reply, dict) else None
    if not isinstance(partial, dict):
        return
    try:
        rejected = int(partial.get("rejectedSpans") or 0)
    except (TypeError, ValueError):
        rejected = 1  # un compteur illisible n'est pas « zéro rejeté »
    if rejected > 0:
        message = partial.get("errorMessage") or "sans message"
        raise OtlpExportError(f"Le collecteur {url} a rejeté des spans ({partial.get('rejectedSpans')}) : {message}.")


def export_otlp(
    ledger: TraceLedger,
    endpoint: str,
    *,
    mission_id: str | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Envoie les traces du *ledger* au collecteur OTLP/HTTP *endpoint*. Rend le nombre de spans acceptés.

    *endpoint* sans chemin reçoit ``/v1/traces`` (convention OTLP) ; avec un
    chemin, il est pris tel quel. Lève :class:`OtlpExportError` au premier lot
    refusé — les lots déjà acceptés ne sont pas annulés, l'erreur le dit via
    le nombre de spans envoyés avant l'échec.
    """
    url = _traces_url(endpoint)
    spans = ledger.otel_spans(mission_id=mission_id)
    sent = 0
    for body in build_otlp_requests(spans, batch_size=batch_size):
        count = sum(len(s["spans"]) for rs in body["resourceSpans"] for s in rs["scopeSpans"])
        try:
            _post(url, body, headers or {}, timeout)
        except OtlpExportError as exc:
            raise OtlpExportError(f"{exc} {sent}/{len(spans)} span(s) acceptés avant l'échec.") from exc
        sent += count
    return sent
