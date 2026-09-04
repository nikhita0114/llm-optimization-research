#!/usr/bin/env bash
# experiments/cluster/install_stack.sh — monitoring + KEDA. Idempotent.
set -euo pipefail
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm upgrade --install mon prometheus-community/kube-prometheus-stack \
  -f experiments/cluster/monitoring-values.yaml \
  -n monitoring --create-namespace
helm upgrade --install keda kedacore/keda -n keda --create-namespace
kubectl -n monitoring wait --for=condition=Available deploy --all --timeout=300s
kubectl -n keda wait --for=condition=Available deploy/keda-operator --timeout=300s
