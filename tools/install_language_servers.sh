#!/usr/bin/env bash
# Offers to install the language servers of Claude Code's code-intelligence plugins, and the
# plugins themselves, one at a time: each step is shown and asked first, never silent
# (docs/pillars/usage.md, "Configuration"). `lws usage doctor` reports what is still missing.
#
#   tools/install_language_servers.sh          # ask for each missing one
#   tools/install_language_servers.sh --check  # only list what is missing
set -euo pipefail

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

# binary | plugin | install command
SERVERS=(
  "pyright-langserver|pyright-lsp|pipx install pyright"
  "typescript-language-server|typescript-lsp|npm install -g typescript-language-server typescript"
  "clangd|clangd-lsp|sudo apt install -y clangd"
  "csharp-ls|csharp-lsp|dotnet tool install --global csharp-ls"
)
MARKETPLACE="claude-plugins-official"
missing=0

ask() {
  local answer
  read -r -p "$1 [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]]
}

for entry in "${SERVERS[@]}"; do
  IFS='|' read -r binary plugin install <<< "$entry"
  if command -v "$binary" >/dev/null 2>&1; then
    echo "ok       $binary"
  else
    missing=$((missing + 1))
    echo "missing  $binary  (install with: $install)"
    if [[ $CHECK_ONLY -eq 0 ]] && ask "Run '$install' now?"; then
      bash -c "$install" || echo "         that failed; install it by hand"
    fi
  fi
  if command -v claude >/dev/null 2>&1 && ! claude plugin list 2>/dev/null | grep -q "$plugin@"; then
    echo "missing  plugin $plugin@$MARKETPLACE"
    if [[ $CHECK_ONLY -eq 0 ]] && ask "Run 'claude plugin install $plugin@$MARKETPLACE' now?"; then
      claude plugin install "$plugin@$MARKETPLACE" || echo "         that failed; install it from Claude Code's /plugin menu"
    fi
  fi
done

[[ $missing -eq 0 ]] || [[ $CHECK_ONLY -eq 0 ]] || exit 1
exit 0
