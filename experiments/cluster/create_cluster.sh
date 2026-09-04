#!/usr/bin/env bash
# experiments/cluster/create_cluster.sh — single-node k3s; traefik and
# metrics-server disabled (unused; saves ~200 MiB). NodePorts are mapped
# through the k3d server container to the host.
set -euo pipefail
k3d cluster create sigscale \
  -p "30080:30080@server:0" \
  -p "30090:30090@server:0" \
  --k3s-arg "--disable=traefik@server:0" \
  --k3s-arg "--disable=metrics-server@server:0" \
  --kubeconfig-update-default
