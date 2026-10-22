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
| `GET /health` | Liveness |
| `GET /events?location=Columbus,%20OH&when=week` | Search (`tonight` \| `week` \| `month` \| `any`) |
| `GET /events?token=…` | Optional Google event-details enrichment |

## Query notes (verified Oct 22, 2026)

- Putting the city in `q` works for Columbus trivia (`bar trivia Columbus, OH`).
- Short `q=trivia night` + `location=` often returns empty from Google Events.
- Results are filtered to items whose title/type contain `trivia`.
- Missing fields are omitted; the app must not invent Free / prizes / ages.

## Deploy (Render)

```bash
cd server
brew install render   # once
render login          # once — authorize in the browser
./deploy.sh           # pushes to GitHub + creates a free Render web service
```

Then set the iOS app’s `TriviaNearbyEventsAPIURL` to `https://trivia-nearby-events.onrender.com` (or the URL Render shows).

`SERPAPI_API_KEY` is set as a Render env var from your local `.env.local` — it is never committed.

Free Render web services may cold-start after idle; the first request after sleep can take ~30–60s.
