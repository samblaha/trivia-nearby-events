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
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
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

# Suburb / neighborhood searches often return stale Google Events rows.
# Always also query the parent metro so the feed stays dense.
METRO_FALLBACKS: dict[str, str] = {
    "westerville": "Columbus, OH",
    "dublin": "Columbus, OH",
    "hilliard": "Columbus, OH",
    "grove city": "Columbus, OH",
    "gahanna": "Columbus, OH",
    "reynoldsburg": "Columbus, OH",
    "upper arlington": "Columbus, OH",
    "worthington": "Columbus, OH",
    "new albany": "Columbus, OH",
    "powell": "Columbus, OH",
    "pickerington": "Columbus, OH",
    "canal winchester": "Columbus, OH",
    "bexley": "Columbus, OH",
    "grandview": "Columbus, OH",
    "short north": "Columbus, OH",
    "german village": "Columbus, OH",
}


def _api_key() -> str:
    key = os.environ.get("SERPAPI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("SERPAPI_API_KEY is not set")
    return key


def _http_get(params: dict[str, str], *, retries: int = 2) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = f"{SERPAPI_SEARCH}?{query}"
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "TriviaNearbyEventsAPI/1.1"},
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            # Retry rate limits / transient upstream failures.
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(0.6 * (attempt + 1))
                last_error = RuntimeError(f"HTTP {exc.code}: {body[:240]}")
                continue
            raise RuntimeError(f"HTTP {exc.code}: {body[:240]}") from exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            raise
    raise RuntimeError(str(last_error) if last_error else "request failed")


def _stable_id(*parts: str) -> str:
    raw = "|".join(p.strip().lower() for p in parts if p and p.strip())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _is_trivia_event(event: dict[str, Any]) -> bool:
    title = str(event.get("title") or "")
    etype = str(event.get("type") or "")
    blob = f"{title} {etype}"
    if not TRIVIA_RE.search(blob):
        return False
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
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def _htichips_for(when: str) -> str | None:
    if when == "tonight":
        return "date:today"
    if when == "week":
        return "date:week"
    if when == "month":
        return "date:month"
    return None


def _search_locations(location: str) -> list[str]:
    """Primary location plus optional parent metro for denser suburb results."""
    locations = [location]
    city = location.split(",")[0].strip().lower()
    metro = METRO_FALLBACKS.get(city)
    if metro and metro.lower() != location.lower():
        locations.append(metro)
    return locations


def _query_shapes(location: str) -> list[str]:
    return [
        f"bar trivia {location}",
        f"pub trivia {location}",
        f"trivia night {location}",
        f"team trivia {location}",
    ]


def _parse_start_hint(event: dict[str, Any], now: datetime) -> datetime | None:
    """Best-effort parse of Google Events date labels for server-side pruning."""
    raw = (event.get("when") or event.get("start_date") or "").strip()
    if not raw:
        return None
    # Keep the start side of ranges.
    for sep in (" – ", " - ", "—", "–"):
        if sep in raw:
            raw = raw.split(sep, 1)[0].strip()
            break
    # Drop weekday prefixes: "Thu, Oct 22, 2026, 7:00 PM"
    if "," in raw and len(raw.split(",")[0]) <= 3:
        raw = raw.split(",", 1)[1].strip()

    # Compare as naive UTC-ish wall times — Google labels have no offset.
    now_naive = now.replace(tzinfo=None)

    formats = (
        "%b %d, %Y, %I:%M %p",
        "%b %d, %Y %I:%M %p",
        "%b %d, %I:%M %p",
        "%b %d %I:%M %p",
        "%b %d, %Y",
        "%b %d",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if "%Y" not in fmt:
            parsed = parsed.replace(year=now_naive.year)
            # Stale undated rows (Jul/Sep in October) should drop, not roll a year.
            if parsed < now_naive - timedelta(days=2):
                bumped = parsed.replace(year=now_naive.year + 1)
                if bumped - now_naive <= timedelta(days=120):
                    parsed = bumped
                else:
                    return parsed  # keep as past → filtered
        return parsed
    return None


def _is_clearly_past(event: dict[str, Any], now: datetime) -> bool:
    parsed = _parse_start_hint(event, now)
    if parsed is None:
        return False
    now_naive = now.replace(tzinfo=None)
    return parsed < now_naive - timedelta(hours=3)


def _fetch_query(
    *,
    q: str,
    chip: str | None,
    key: str,
    attempts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
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
    except Exception as exc:  # noqa: BLE001
        attempts.append({"q": q, "error": str(exc)})
        return []

    if payload.get("error"):
        attempts.append({"q": q, "error": payload["error"]})
        if not chip:
            return []
        # Retry once without the date chip — Google often blanks chip filters.
        params.pop("htichips", None)
        try:
            payload = _http_get(params)
        except Exception as exc:  # noqa: BLE001
            attempts.append({"q": q, "retry_error": str(exc)})
            return []
        if payload.get("error"):
            attempts.append({"q": q, "retry_error": payload["error"]})
            return []

    raw_events = payload.get("events_results") or []
    normalized = [n for n in (_normalize_search_event(e) for e in raw_events) if n]
    attempts.append({"q": q, "raw": len(raw_events), "trivia": len(normalized)})
    return normalized


def search_events(location: str, when: str = "week") -> dict[str, Any]:
    """Run Google Events via SerpApi using query shapes that work for pub trivia."""
    location = location.strip()
    if not location:
        raise ValueError("location is required")

    key = _api_key()
    chip = _htichips_for(when)
    collected: list[dict[str, Any]] = []
    used_queries: list[str] = []
    attempts: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for loc in _search_locations(location):
        for q in _query_shapes(loc):
            batch = _fetch_query(q=q, chip=chip, key=key, attempts=attempts)
            if batch:
                used_queries.append(q)
                collected.extend(batch)
        # Enough density from the primary location — still allow metro merge below.
        if loc == location and len(collected) >= 12:
            break

    # De-dupe by occurrence id while preserving order.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for event in collected:
        if event["id"] in seen:
            continue
        seen.add(event["id"])
        # Drop obviously stale Google rows (Jul/Sep leftovers in October).
        if _is_clearly_past(event, now):
            continue
        unique.append(event)

    return {
        "source": "serpapi_google_events",
        "location": location,
        "when": when,
        "query_used": " | ".join(used_queries[:4]) if used_queries else None,
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
