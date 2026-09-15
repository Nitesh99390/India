#!/bin/bash
# Tiny helper for the Mini App dev harness.
#   tests/devctl.sh start <role> <port>   → start tests/dev_server.py in that scenario (background)
#   tests/devctl.sh stop  <port>          → stop the harness bound to that port
#   tests/devctl.sh stopall               → stop every harness
cd "$(dirname "$0")/.." || exit 1
cmd=$1
case "$cmd" in
  start)
    role=${2:-owner}; port=${3:-8080}
    DEV_ROLE="$role" PORT="$port" nohup python3 tests/dev_server.py > "/tmp/dev_${role}.log" 2>&1 &
    echo "started role=$role port=$port pid=$! log=/tmp/dev_${role}.log"
    ;;
  stop)
    port=${2:-8080}
    for p in $(pgrep -f "python3 tests/dev_server.py"); do
      if tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null | grep -qx "PORT=$port"; then kill "$p" && echo "stopped pid=$p port=$port"; fi
    done
    ;;
  stopall)
    pgrep -f "python3 tests/dev_server.py" | xargs -r kill
    echo "stopped all"
    ;;
  *) echo "usage: $0 start <role> <port> | stop <port> | stopall"; exit 1 ;;
esac
