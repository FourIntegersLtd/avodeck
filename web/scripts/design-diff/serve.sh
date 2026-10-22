#!/bin/bash
# Build the app into its own folder and serve it, so several people or agents can each have a copy.
#   PORT=3101 scripts/design-diff/serve.sh   # then: pnpm design-diff --base http://127.0.0.1:3101 <id>
cd "$(dirname "$0")/../.." || exit 1
PORT=${PORT:-3001}
export NEXT_DIST_DIR=".next-$PORT"
[ -f "$NEXT_DIST_DIR/server.pid" ] && kill "$(cat "$NEXT_DIST_DIR/server.pid")" 2>/dev/null
sleep 1
if ! npx next build > "/tmp/next-build-$PORT.log" 2>&1; then
  echo "BUILD FAILED"; tail -30 "/tmp/next-build-$PORT.log"; exit 2
fi
nohup node node_modules/next/dist/bin/next start -p "$PORT" > "/tmp/next-start-$PORT.log" 2>&1 &
echo $! > "$NEXT_DIST_DIR/server.pid"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  curl -s -o /dev/null "http://127.0.0.1:$PORT/sign-in" && { echo "serving on $PORT"; exit 0; }
done
echo "server did not start"; tail -10 "/tmp/next-start-$PORT.log"; exit 3
