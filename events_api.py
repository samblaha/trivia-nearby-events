#!/usr/bin/env python3
"""Single serverless-style endpoint that proxies SerpApi Google Events.

Keep SERPAPI_API_KEY on the server. The iOS app never sees it.

  GET /events?location=Columbus,%20OH&when=week
  GET /events?location=Columbus,%20OH&when=tonight
  GET /events?token=<event_token>   # optional details enrichment

Local:
  SERPAPI_API_KEY=... python3 server/events_api.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SERPAPI_SEARCH = "https://serpapi.com/search.json"
# Default to all interfaces so a physical iPhone / Railway can reach the API.
# Railway injects PORT; local/dev can still use EVENTS_API_PORT.
HOST = os.environ.get("EVENTS_API_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT") or os.environ.get("EVENTS_API_PORT", "8787"))

TRIVIA_RE = re.compile(r"\btrivia\b", re.I)
NON_TRIVIA_HINTS = re.compile(
    r"\b(comedy|concert|tour|festival|workshop|class|market|food tour)\b",
    re.I,
)


def _api_key() -> str:
    key = os.environ.get("SERPAPI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("SERPAPI_API_KEY is not set")
    return key


def _http_get(params: dict[str, str]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{SERPAPI_SEARCH}?{query}",
        headers={"User-Agent": "TriviaNearbyEventsAPI/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _stable_id(*parts: str) -> str:
    raw = "|".join(p.strip().lower() for p in parts if p and p.strip())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _is_trivia_event(event: dict[str, Any]) -> bool:
    title = str(event.get("title") or "")
    etype = str(event.get("type") or "")
    blob = f"{title} {etype}"
    if not TRIVIA_RE.search(blob):
        return False
    # Keep music trivia / themed trivia; drop obvious non-trivia noise
    # when "trivia" is absent from title but present only weakly — already required.
    if NON_TRIVIA_HINTS.search(title) and not TRIVIA_RE.search(title):
        return False
    return True


def _normalize_search_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not _is_trivia_event(raw):
        return None
    title = (raw.get("title") or "").strip()
    venue = raw.get("venue") or {}
    venue_name = (venue.get("name") or "").strip()
    if not title or not venue_name:
        return None
    date = raw.get("date") or {}
    address = raw.get("address") or []
    if isinstance(address, str):
        address = [address]
    address = [a for a in address if isinstance(a, str) and a.strip()]

    series_id = _stable_id(venue_name, title)
    start_date = (date.get("start_date") or "").strip()
    when = (date.get("when") or "").strip()
    occurrence_id = _stable_id(venue_name, title, start_date or when)

    ticket_info = []
    for ticket in raw.get("ticket_info") or []:
        if not isinstance(ticket, dict):
            continue
        link = ticket.get("link")
        source = ticket.get("source")
        if link and source:
            ticket_info.append({"source": source, "link": link})

    out: dict[str, Any] = {
        "id": occurrence_id,
        "series_id": series_id,
        "title": title,
        "venue_name": venue_name,
        "when": when or None,
        "start_date": start_date or None,
        "address": address,
        "event_token": raw.get("event_token"),
    }
    # Optional fields only when present in the source payload.
    if raw.get("type"):
        out["type"] = raw["type"]
    if raw.get("thumbnail"):
        out["thumbnail"] = raw["thumbnail"]
    if raw.get("price"):
        out["price"] = raw["price"]
    if raw.get("extracted_price") is not None:
        out["extracted_price"] = raw["extracted_price"]
    if ticket_info:
        out["ticket_info"] = ticket_info
    map_info = raw.get("event_location_map") or {}
    if map_info.get("link"):
        out["map_link"] = map_info["link"]
    return out


def _normalize_details(raw: dict[str, Any]) -> dict[str, Any]:
    details = raw.get("event_details") or {}
    address = details.get("address") or []
    if isinstance(address, str):
        address = [address]
    venue = details.get("venue") or {}
    ticket_info = []
    for ticket in details.get("ticket_info") or []:
        if isinstance(ticket, dict) and ticket.get("link") and ticket.get("source"):
            ticket_info.append({"source": ticket["source"], "link": ticket["link"]})

    out: dict[str, Any] = {
        "title": details.get("title"),
        "when": (details.get("date") or {}).get("when"),
        "start_date": (details.get("date") or {}).get("start_date"),
        "address": [a for a in address if isinstance(a, str) and a.strip()],
        "venue_name": (venue.get("name") if isinstance(venue, dict) else None),
    }
    if details.get("description"):
        out["description"] = details["description"]
    if details.get("thumbnail"):
        out["thumbnail"] = details["thumbnail"]
    if ticket_info:
        out["ticket_info"] = ticket_info
    if isinstance(venue, dict):
        if venue.get("link"):
            out["venue_link"] = venue["link"]
        if venue.get("rating") is not None:
            out["venue_rating"] = venue["rating"]
    local_map = details.get("local_map") or {}
    if local_map.get("link"):
        out["map_link"] = local_map["link"]
    # Drop nulls so clients can treat absence as unknown.
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def _htichips_for(when: str) -> str | None:
    if when == "tonight":
        return "date:today"
    if when == "week":
        return "date:week"
    if when == "month":
        return "date:month"
    return None


def search_events(location: str, when: str = "week") -> dict[str, Any]:
    """Run Google Events via SerpApi using query shapes that work for pub trivia."""
    location = location.strip()
    if not location:
        raise ValueError("location is required")

    key = _api_key()
    # Empirically, putting the city in `q` works for Columbus trivia;
    # `location=` + short `q=trivia night` often returns empty.
    queries = [
        f"bar trivia {location}",
        f"trivia night {location}",
        f"pub trivia {location}",
    ]
    chip = _htichips_for(when)
    collected: list[dict[str, Any]] = []
    used_query = None
    attempts: list[dict[str, Any]] = []

    for q in queries:
        params: dict[str, str] = {
            "engine": "google_events",
            "q": q,
            "hl": "en",
            "gl": "us",
            "api_key": key,
        }
        if chip:
            params["htichips"] = chip
        try:
            payload = _http_get(params)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            attempts.append({"q": q, "error": f"HTTP {exc.code}", "body": body[:240]})
            continue
        except Exception as exc:  # noqa: BLE001
            attempts.append({"q": q, "error": str(exc)})
            continue

        if payload.get("error"):
            attempts.append({"q": q, "error": payload["error"]})
            # Retry same query without date chip once.
            if chip:
                params.pop("htichips", None)
                try:
                    payload = _http_get(params)
                except Exception as exc:  # noqa: BLE001
                    attempts.append({"q": q, "retry_error": str(exc)})
                    continue
                if payload.get("error"):
                    attempts.append({"q": q, "retry_error": payload["error"]})
                    continue
            else:
                continue

        raw_events = payload.get("events_results") or []
        normalized = [n for n in (_normalize_search_event(e) for e in raw_events) if n]
        attempts.append({"q": q, "raw": len(raw_events), "trivia": len(normalized)})
        if normalized:
            collected = normalized
            used_query = q
            break

    # De-dupe by occurrence id while preserving order.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for event in collected:
        if event["id"] in seen:
            continue
        seen.add(event["id"])
        unique.append(event)

    return {
        "source": "serpapi_google_events",
        "location": location,
        "when": when,
        "query_used": used_query,
        "count": len(unique),
        "events": unique,
        "attempts": attempts,
    }


def event_details(token: str) -> dict[str, Any]:
    token = token.strip()
    if not token:
        raise ValueError("token is required")
    payload = _http_get(
        {
            "engine": "google_event_details",
            "event_token": token,
            "hl": "en",
            "gl": "us",
            "api_key": _api_key(),
        }
    )
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return {
        "source": "serpapi_google_event_details",
        "details": _normalize_details(payload),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys_stderr = __import__("sys").stderr
        print(f"[events-api] {self.address_string()} {fmt % args}", file=sys_stderr)

    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in ("/events", "/health"):
            self._send(404, {"error": "not_found", "message": "Use GET /events"})
            return
        if parsed.path == "/health":
            self._send(200, {"ok": True})
            return

        qs = urllib.parse.parse_qs(parsed.query)
        token = (qs.get("token") or [""])[0]
        try:
            if token:
                self._send(200, event_details(token))
                return
            location = (qs.get("location") or [""])[0]
            when = ((qs.get("when") or ["week"])[0] or "week").lower()
            if when not in {"tonight", "week", "month", "any"}:
                when = "week"
            self._send(200, search_events(location=location, when=when))
        except ValueError as exc:
            self._send(400, {"error": "bad_request", "message": str(exc)})
        except RuntimeError as exc:
            self._send(502, {"error": "upstream", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": "server_error", "message": str(exc)})


def main() -> None:
    _api_key()  # fail fast
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Trivia Nearby events API on http://{HOST}:{PORT}/events")
    server.serve_forever()


if __name__ == "__main__":
    main()
