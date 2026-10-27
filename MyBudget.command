#!/bin/zsh
# Starts Docker if needed, builds/starts MyBudget, and opens it in the browser.
cd "$(dirname "$0")"
URL=$(grep -E '^BASE_URL=' .env 2>/dev/null | cut -d= -f2-); URL=${URL:-http://localhost:8765}
export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"

if ! command -v docker >/dev/null; then
  echo "Docker isn't installed. Get Docker Desktop from https://www.docker.com/products/docker-desktop/ and run this again."
  open "https://www.docker.com/products/docker-desktop/"; read -k1 "?Press any key to close."; exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Starting Docker…"; open -a Docker
  for i in {1..90}; do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker info >/dev/null 2>&1 || { echo "Docker didn't start. Open Docker Desktop and try again."; read -k1 "?Press any key."; exit 1; }
fi
mkdir -p data
echo "Starting MyBudget…"
docker compose up -d --build || { read -k1 "?Something went wrong. Press any key."; exit 1; }
for i in {1..30}; do curl -fs "$URL/health" >/dev/null && break; sleep 1; done
open "$URL"
echo "MyBudget is running at $URL. It keeps running in Docker; stop it with: docker compose down"
sleep 3
osascript -e 'tell application "Terminal" to close (every window whose name contains "MyBudget.command")' >/dev/null 2>&1 &
