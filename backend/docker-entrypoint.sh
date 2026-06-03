#!/bin/sh
set -e

# Hand off to CMD (uvicorn). `exec` keeps uvicorn as PID 1 for clean
# signal handling.
# Database migrations are run by the separate db-migration compose service
# (not in the entrypoint) so that the app only starts after a successful
# migration.
exec "$@"
