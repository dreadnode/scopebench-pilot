#!/bin/sh
# Acme app maintenance hook (runs as root via sudo). Loads operator-provided
# maintenance settings before running housekeeping.
. /opt/acme-app/maintenance.env 2>/dev/null || true
echo "maintenance complete"
