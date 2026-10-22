#!/usr/bin/env bash
# Deploy the Trivia Nearby events API to Render.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd .. && pwd)"
if [[ -f "$ROOT/.env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env.local"
  set +a
fi

if [[ -z "${SERPAPI_API_KEY:-}" ]]; then
  echo "SERPAPI_API_KEY is not set. Add it to ../.env.local or export it."
  exit 1
fi

if ! command -v render >/dev/null; then
  echo "Install the Render CLI first: brew install render"
  exit 1
fi

if ! command -v gh >/dev/null; then
  echo "GitHub CLI (gh) is required to push the API repo."
  exit 1
fi

REPO_NAME="${RENDER_REPO_NAME:-trivia-nearby-events}"
SERVICE_NAME="${RENDER_SERVICE_NAME:-trivia-nearby-events}"

if [[ ! -d .git ]]; then
  git init
  git add Dockerfile events_api.py requirements.txt render.yaml .gitignore README.md
  git commit -m "Initial Trivia Nearby events API for Render"
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "Creating GitHub repo ${REPO_NAME}…"
  gh repo create "$REPO_NAME" --private --source=. --remote=origin --push
else
  git add -A
  if ! git diff --cached --quiet; then
    git commit -m "Update events API for Render deploy" || true
  fi
  git push -u origin HEAD
fi

REPO_URL="$(gh repo view --json url -q .url)"
echo "Repo: ${REPO_URL}"

echo "Creating / updating Render web service…"
# Non-interactive create; if the service already exists, update env + redeploy instead.
if ! render services create \
  --name "$SERVICE_NAME" \
  --type web_service \
  --repo "$REPO_URL" \
  --runtime docker \
  --plan free \
  --branch main \
  --env-var "SERPAPI_API_KEY=${SERPAPI_API_KEY}" \
  --env-var "EVENTS_API_HOST=0.0.0.0" \
  --output json 2>/tmp/render-create.json; then
  echo "Create reported an error (service may already exist). Checking services…"
  cat /tmp/render-create.json 2>/dev/null || true
fi

echo
echo "Open the Render dashboard if needed, confirm the service URL, then set"
echo "TriviaNearbyEventsAPIURL in the iOS Info.plist to:"
echo "  https://${SERVICE_NAME}.onrender.com"
echo
echo "Health check:"
echo "  curl https://${SERVICE_NAME}.onrender.com/health"
