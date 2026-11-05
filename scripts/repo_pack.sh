#!/bin/bash
# Phase 7 (2026-11-04): pack the versioned Line Watch files into _sync/linewatch-sync.tar.gz
# so a cloud session can push them to github.com/neuegraphics-dev/sportsbook-nfl-research-sklill.
# The Mac cannot reach GitHub (network allowlist), so the cloud does the push.
# Versioned: scripts (no .bak / scratch _*.py), model, data (game JSON only), results, docs,
# reports, dashboard/index.html. Not versioned: _backups, scratch drivers, generated per-game charts.
cd "$(dirname "$0")/.." || exit 1
mkdir -p _sync
tar czf _sync/linewatch-sync.tar.gz \
  --exclude='*.bak*' --exclude='__pycache__' --exclude='scripts/_*' --exclude='*.b64' --exclude='*-graph.html' --exclude='*.tmp' \
  scripts model data results docs reports dashboard/index.html
echo "{\"packed\": \"_sync/linewatch-sync.tar.gz\", \"bytes\": $(wc -c < _sync/linewatch-sync.tar.gz)}"
