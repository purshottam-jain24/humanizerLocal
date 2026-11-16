#!/usr/bin/env bash
# Start HumanWrite. Run:  ./start.sh
exec "$(cd "$(dirname "$0")" && pwd)/scripts/dev.sh" "$@"
