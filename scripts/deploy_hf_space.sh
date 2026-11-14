#!/usr/bin/env bash
# Deploy the single-container image to a Hugging Face Docker Space.
#   HF_USER=<you> HF_SPACE=gigbid HF_TOKEN=<write token> ./scripts/deploy_hf_space.sh
# Then set Space secrets (Settings -> Variables and secrets): DATABASE_URL (Neon), GROQ_API_KEY, SESSION_SECRET,
# TOKEN_ENCRYPTION_KEY, REVIEWER_EMAIL, REVIEWER_PASSWORD, GIGSIM_ADMIN_TOKEN, MARKETPLACE_SERVICE_TOKEN.
set -euo pipefail
: "${HF_USER:?}" "${HF_SPACE:?}" "${HF_TOKEN:?}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
git -C "$ROOT" archive HEAD | tar -x -C "$WORK"
cp "$ROOT/infra/docker/app.Dockerfile" "$WORK/Dockerfile"
cat > "$WORK/README.md" <<'MD'
---
title: GigBid
emoji: 🧭
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---
GigBid — multi-agent freelancer copilot. Source and docs: see the GitHub repository.
MD
cd "$WORK"
git init -q -b main && git add -A && git -c user.name=deploy -c user.email=deploy@localhost commit -q -m "deploy $(git -C "$ROOT" rev-parse --short HEAD)"
git push -f "https://${HF_USER}:${HF_TOKEN}@huggingface.co/spaces/${HF_USER}/${HF_SPACE}" main
echo "Deployed. Build logs: https://huggingface.co/spaces/${HF_USER}/${HF_SPACE}?logs=build"
echo "App URL:            https://${HF_USER}-${HF_SPACE}.hf.space"
