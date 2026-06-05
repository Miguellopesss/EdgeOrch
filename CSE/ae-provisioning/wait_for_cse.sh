#!/usr/bin/env bash
set -euo pipefail

host="127.0.0.1"
port="8080"
attempts=60
sleep_seconds=2

for ((i=1; i<=attempts; i++)); do
  if timeout 1 bash -c "</dev/tcp/${host}/${port}" 2>/dev/null; then
    echo "[INFO] ACME CSE is reachable on ${host}:${port}"
    exit 0
  fi

  echo "[INFO] Waiting for ACME CSE on ${host}:${port} (${i}/${attempts})"
  sleep "${sleep_seconds}"
done

echo "[ERROR] ACME CSE did not become reachable on ${host}:${port}" >&2
exit 1
