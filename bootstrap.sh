#!/usr/bin/env bash
# Installs the two tools the workshop needs, in user space (no sudo):
#   uv          - Python package/runtime manager
#   agents-cli  - builds, runs, evaluates and deploys the ADK agent
# Works in Cloud Shell and on macOS/Linux. Safe to re-run.
set -euo pipefail

AGENTS_CLI_VERSION="1.7.0"
export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
echo "uv: $(uv --version)"

if ! command -v agents-cli >/dev/null 2>&1 || ! agents-cli --version | grep -q "$AGENTS_CLI_VERSION"; then
  echo "Installing agents-cli $AGENTS_CLI_VERSION..."
  uv tool install --force "google-agents-cli==$AGENTS_CLI_VERSION"
fi
echo "agents-cli: $(agents-cli --version)"

echo "Installing Python dependencies..."
uv sync --quiet

# Link the repo's agents-cli skills into Antigravity IDE/2.0 and CLI skill paths
# so every Antigravity surface sees them without needing npx at runtime.
REPO_SKILLS="$(pwd)/.agents/skills"
if [[ -d "$REPO_SKILLS" ]]; then
  for target in "$HOME/.gemini/config/skills" "$HOME/.gemini/antigravity-cli/skills"; do
    mkdir -p "$target"
    for s in "$REPO_SKILLS"/google-agents-cli-*; do
      [[ -d "$s" ]] && ln -sfn "$s" "$target/$(basename "$s")"
    done
  done
  echo "Linked $(ls -1d "$REPO_SKILLS"/google-agents-cli-* | wc -l) agents-cli skills for Antigravity."
fi

if ! grep -q 'HOME/.local/bin' "$HOME/.bashrc" 2>/dev/null; then
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
fi

cat <<'EOF'

Done. Next:
  gcloud auth login --update-adc --no-launch-browser
  gcloud config set project <workshop-project-id>
  make configure && make whoami
EOF
