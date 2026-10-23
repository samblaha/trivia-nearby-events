# Trivia Nearby — Events API

One serverless-style endpoint that proxies SerpApi’s Google Events engine and returns trivia-only results. The SerpApi key stays on the server.

## Run locally

```bash
cd "/Users/samblaha/Documents/Development/Trivia Night Finder"
set -a; source .env.local; set +a   # provides SERPAPI_API_KEY
EVENTS_API_HOST=0.0.0.0 python3 server/events_api.py
```

Listens on `http://0.0.0.0:8787` by default (or `PORT` / `EVENTS_API_PORT`).

## Endpoints

| Request | Purpose |
| --- | --- |
| `GET /health` | Liveness (fast; the iOS app already pings this) |
| `GET /warm` | Optional cache prime (`location`, `when`; defaults Columbus, OH + `week`) |
| `GET /events?location=Columbus,%20OH&when=week` | Search (`tonight` \| `week` \| `month` \| `any`) |
| `GET /events?token=…` | Optional Google event-details enrichment |
| `GET /.well-known/apple-app-site-association` | Universal links (same as `/apple-app-site-association`) |
| `GET /invite` | Browser fallback page for invite links |
| `GET /privacy` | Privacy overview (linked from the iOS Profile tab) |
| `GET /support` | Support overview (linked from the iOS Profile tab) |

## SerpApi cost / cold-start mitigations

- **In-memory search cache:** `(normalized location, when)` → full JSON response, TTL **10 minutes** (`EVENTS_SEARCH_CACHE_TTL_SEC`, default 600). Cache hits skip SerpApi and respond with `Cache-Control: private, max-age=60`.
- **Fewer queries:** only `bar trivia {location}` and `trivia night {location}` (two shapes per location; metro fallback unchanged).
- **Early exit:** stops querying after enough trivia rows on the primary location (default **8**, `EVENTS_EARLY_EXIT_MIN`).
- **Warm:** `GET /warm` runs one cached search for the default metro; use after deploy or idle spin-up if you want listings ready before user traffic.

## Query notes (verified Oct 22, 2026)

- Putting the city in `q` works for Columbus trivia (`bar trivia Columbus, OH`).
- Short `q=trivia night` + `location=` often returns empty from Google Events.
- Results are filtered to items whose title/type contain `trivia`.
- Missing fields are omitted; the app must not invent Free / prizes / ages.

## Deploy (Render)

**Live:** https://trivia-nearby-events.onrender.com  
**Dashboard:** https://dashboard.render.com/web/srv-dbd45u2vcj2c73eq56t0  
**Repo:** https://github.com/samblaha/trivia-nearby-events

```bash
cd server
# After code changes:
git add -A && git commit -m "Update events API" && git push
# Render auto-deploys from main. Or: render deploys create srv-dbd45u2vcj2c73eq56t0 --wait
```

iOS `TriviaNearbyEventsAPIURL` → `https://trivia-nearby-events.onrender.com`

`SERPAPI_API_KEY` lives only as a Render env var (never in git).

Free Render web services may cold-start after idle; the first request after sleep can take ~30–60s.
