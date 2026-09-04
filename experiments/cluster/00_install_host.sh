#!/usr/bin/env bash
# experiments/cluster/00_install_host.sh — host-side tooling for the rig.
# Idempotent: skips anything already present. Installs to ~/.local/bin.
set -euo pipefail
BIN="$HOME/.local/bin"; mkdir -p "$BIN"

need() { command -v "$1" >/dev/null 2>&1; }

if ! need k3d; then
  curl -sL "https://github.com/k3d-io/k3d/releases/download/v5.8.3/k3d-linux-amd64" \
    -o "$BIN/k3d" && chmod +x "$BIN/k3d"
fi
if ! need kubectl; then
  curl -sL "https://dl.k8s.io/release/v1.31.0/bin/linux/amd64/kubectl" \
    -o "$BIN/kubectl" && chmod +x "$BIN/kubectl"
fi
if ! need helm; then
  curl -sL "https://get.helm.sh/helm-v3.16.2-linux-amd64.tar.gz" | tar xz -C /tmp
  install -m 0755 /tmp/linux-amd64/helm "$BIN/helm"
fi
if [ ! -x .venv/bin/guidellm ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install "guidellm[recommended]" pytest pandas requests PyYAML
fi
echo "== versions =="
k3d version; kubectl version --client=true -o yaml | grep gitVersion; helm version --short
.venv/bin/guidellm --version; .venv/bin/python --version
