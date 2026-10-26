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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from business_events import search_business_events, merge_business_events
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
SEARCH_CACHE_TTL_SEC = int(os.environ.get("EVENTS_SEARCH_CACHE_TTL_SEC", "600"))
DEFAULT_WARM_LOCATION = os.environ.get("EVENTS_WARM_LOCATION", "Columbus, OH")
DEFAULT_WARM_WHEN = os.environ.get("EVENTS_WARM_WHEN", "week")
EARLY_EXIT_MIN_EVENTS = int(os.environ.get("EVENTS_EARLY_EXIT_MIN", "8"))
SEARCH_DEADLINE_SEC = 20

_search_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()

APPLE_APP_SITE_ASSOCIATION: dict[str, Any] = {
    "applinks": {
        "apps": [],
        "details": [
            {
                "appID": "QWC625J4YU.com.trivianearby.app",
                "paths": ["/invite", "/invite/*"],
            }
        ],
    }
}

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


def _retry_pause(attempt: int, deadline: float | None) -> None:
    delay = 0.6 * (attempt + 1)
    if deadline is not None:
        delay = min(delay, max(0, deadline - time.monotonic()))
    time.sleep(delay)


def _http_get(
    params: dict[str, str], *, retries: int = 2, deadline: float | None = None
) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = f"{SERPAPI_SEARCH}?{query}"
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        remaining = deadline - time.monotonic() if deadline is not None else 45
        if remaining <= 0:
            raise TimeoutError("Event search deadline exceeded")
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "TriviaNearbyEventsAPI/1.1"},
        )
        try:
            with urllib.request.urlopen(req, timeout=min(12, remaining) if deadline is not None else 45) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            # Retry rate limits / transient upstream failures.
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries:
                _retry_pause(attempt, deadline)
                last_error = RuntimeError(f"HTTP {exc.code}: {body[:240]}")
                continue
            raise RuntimeError(f"HTTP {exc.code}: {body[:240]}") from exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                _retry_pause(attempt, deadline)
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
        f"trivia night {location}",
    ]


def _normalize_location_key(location: str) -> str:
    return " ".join(location.strip().lower().split())


def _search_cache_get(location: str, when: str) -> dict[str, Any] | None:
    key = (_normalize_location_key(location), when.lower())
    now = time.monotonic()
    with _cache_lock:
        entry = _search_cache.get(key)
        if not entry:
            return None
        expires_at, body = entry
        if now >= expires_at:
            _search_cache.pop(key, None)
            return None
        return json.loads(json.dumps(body))


def _search_cache_set(location: str, when: str, body: dict[str, Any]) -> None:
    key = (_normalize_location_key(location), when.lower())
    expires_at = time.monotonic() + SEARCH_CACHE_TTL_SEC
    with _cache_lock:
        _search_cache[key] = (expires_at, json.loads(json.dumps(body)))


def warm_search_cache(
    location: str | None = None,
    when: str | None = None,
) -> dict[str, Any]:
    loc = (location or DEFAULT_WARM_LOCATION).strip()
    w = (when or DEFAULT_WARM_WHEN).lower()
    if w not in {"tonight", "week", "month", "any"}:
        w = "week"
    body = search_events(location=loc, when=w, use_cache=False)
    return {"ok": True, "warmed": {"location": loc, "when": w, "count": body.get("count", 0)}}


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
    deadline: float | None = None,
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
        payload = _http_get(params, deadline=deadline)
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
            payload = _http_get(params, deadline=deadline)
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


def search_events(
    location: str,
    when: str = "week",
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Run Google Events via SerpApi using query shapes that work for pub trivia."""
    location = location.strip()
    if not location:
        raise ValueError("location is required")

    when = when.lower()
    if when not in {"tonight", "week", "month", "any"}:
        when = "week"

    if use_cache:
        cached = _search_cache_get(location, when)
        if cached is not None:
            return cached

    key = _api_key()
    chip = _htichips_for(when)
    collected: list[dict[str, Any]] = []
    used_queries: list[str] = []
    attempts: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    deadline = time.monotonic() + SEARCH_DEADLINE_SEC

    for loc in _search_locations(location):
        for q in _query_shapes(loc):
            if time.monotonic() >= deadline:
                break
            batch = _fetch_query(q=q, chip=chip, key=key, attempts=attempts, deadline=deadline)
            if batch:
                used_queries.append(q)
                collected.extend(batch)
        # Enough density from the primary location — still allow metro merge below.
        if time.monotonic() >= deadline or (loc == location and len(collected) >= EARLY_EXIT_MIN_EVENTS):
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

    if not unique and attempts and all('error' in attempt or 'retry_error' in attempt for attempt in attempts):
        raise RuntimeError("Event search is temporarily unavailable. Please try again.")

    body = {
        "source": "serpapi_google_events",
        "location": location,
        "when": when,
        "query_used": " | ".join(used_queries[:4]) if used_queries else None,
        "count": len(unique),
        "events": unique,
        "attempts": attempts,
    }
    if use_cache:
        _search_cache_set(location, when, body)
    return body


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

    def _send_json(
        self,
        status: int,
        body: dict[str, Any],
        *,
        cache_control: str = "no-store",
    ) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, status: int, html: str, *, cache_control: str = "no-store") -> None:
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _aasa_body() -> bytes:
        return json.dumps(APPLE_APP_SITE_ASSOCIATION, separators=(",", ":")).encode("utf-8")

    def _send_aasa(self) -> None:
        data = self._aasa_body()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _invite_html() -> str:
        from invite_landing import INVITE_HTML
        return INVITE_HTML

    @staticmethod
    def _privacy_html() -> str:
        return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trivia Nearby Privacy</title>
</head>
<body>
  <main>
    <h1>Privacy overview</h1>
    <p>Trivia Nearby uses your location only to find nearby trivia nights. Precise coordinates are not sent to the listings proxy; the app sends a place label (city or ZIP text).</p>
    <p>Browsing does not require an account. Supabase stores your email, account identifier, player profile, and saved nights when you create an account. Saved nights remain private. You can delete your account in Profile.</p>
    <p>Shared teams store team names, membership, planned nights, and RSVPs in Supabase. Teammates can see player display names and team responses, but not account emails or private saved nights. Anyone with an invitation link can preview the team name and member count; an account is required to join. The captain can replace invitation links and remove members. Leaving removes your membership and team RSVPs. Deleting the captain’s account deletes their teams.</p>
    <p>AI team-name suggestions run on your device using Apple Intelligence. Your inspiration text is not sent to our server. A name you choose is stored in Supabase when you create a shared team.</p>
    <p>A local copy of saved nights, invites, preferences, and the last live search results is stored on your device. MapKit may make its own network requests for maps, search, and directions.</p>
    <h2>Business accounts and listings</h2>
    <p>For venue owners and trivia hosts, Supabase stores your business name, account email, business type, city, signup source, event schedules, and ownership-review submissions. Published event details, including venue addresses, descriptions and booking links, are public. Draft and paused listings are excluded from our public business feed. Other users cannot read your private account or billing records.</p>
    <p>If you have a business subscription, Stripe processes payment details. We store customer and subscription identifiers, plan, status and paid-through dates; we do not store full payment-card numbers. New paid promotions are currently unavailable.</p>
    <p>Account emails are delivered through Resend. Support messages, including your email address and message content, are handled in Zoho Mail. Render hosts our websites and API; hosting and email providers may keep operational request or delivery logs. Contact <a href="mailto:support@trivianearby.app">support@trivianearby.app</a> for access, correction or deletion requests. Business account deletion requires reconciling any active billing first.</p>
    <p>Listings are sourced from Google Events and participating venues or hosts via a server proxy. We do not sell personal data.</p>
  </main>
</body>
</html>
"""

    @staticmethod
    def _support_html() -> str:
        return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trivia Nearby Support</title>
</head>
<body>
  <main>
    <h1>Support</h1>
    <p>Trivia Nearby helps you find pub trivia near you.</p>
    <p>If listings look wrong or missing, pull to refresh on Discover, widen your radius, or try a nearby city. Schedules come from Google Events and can change without notice—always confirm with the venue.</p>
    <p>For app or account help, email <a href="mailto:support@trivianearby.app">support@trivianearby.app</a>. Include what happened, your device and app version, and the event details when relevant. Do not send passwords or confirmation codes.</p>
    <p>Venue owners and trivia hosts can find event-management help at <a href="https://trivianearby.app/support">Trivia Nearby for business support</a>.</p>
  </main>
</body>
</html>
"""

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path in ("/.well-known/apple-app-site-association", "/apple-app-site-association"):
            self._send_aasa()
            return
        if path == "/invite" or path.startswith("/invite/"):
            self._send_html(200, self._invite_html())
            return
        if path == "/privacy":
            self._send_html(200, self._privacy_html(), cache_control="public, max-age=3600")
            return
        if path == "/support":
            self._send_html(200, self._support_html(), cache_control="public, max-age=3600")
            return
        if path == "/health":
            self._send_json(200, {"ok": True})
            return
        if path == "/warm":
            qs = urllib.parse.parse_qs(parsed.query)
            location = (qs.get("location") or [""])[0] or None
            when = (qs.get("when") or [""])[0] or None
            try:
                self._send_json(200, warm_search_cache(location=location, when=when))
            except ValueError as exc:
                self._send_json(400, {"error": "bad_request", "message": str(exc)})
            except RuntimeError as exc:
                self._send_json(502, {"error": "upstream", "message": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"error": "server_error", "message": str(exc)})
            return
        if path != "/events":
            self._send_json(404, {"error": "not_found", "message": "Use GET /events"})
            return

        qs = urllib.parse.parse_qs(parsed.query)
        token = (qs.get("token") or [""])[0]
        try:
            if token:
                self._send_json(200, event_details(token))
                return
            location = (qs.get("location") or [""])[0]
            if not location.strip():
                raise ValueError("location is required")
            when = ((qs.get("when") or ["week"])[0] or "week").lower()
            if when not in {"tonight", "week", "month", "any"}:
                when = "week"
            # Fetch the public business projection on each search, including
            # Google-cache hits, so pauses and entitlement changes take effect.
            managed = search_business_events(location, when)
            cached = _search_cache_get(location, when)
            if cached is not None:
                self._send_json(200, merge_business_events(cached, managed), cache_control="no-store")
                return
            try:
                body = search_events(location=location, when=when)
            except RuntimeError:
                if not managed:
                    raise
                body = {"source": "business_events", "location": location, "when": when, "events": []}
            self._send_json(200, merge_business_events(body, managed), cache_control="no-store")
        except ValueError as exc:
            self._send_json(400, {"error": "bad_request", "message": str(exc)})
        except RuntimeError as exc:
            self._send_json(502, {"error": "upstream", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"error": "server_error", "message": str(exc)})


def main() -> None:
    _api_key()  # fail fast
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Trivia Nearby events API on http://{HOST}:{PORT}/events")
    server.serve_forever()


if __name__ == "__main__":
    main()
