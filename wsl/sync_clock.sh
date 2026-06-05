#!/bin/bash
# Resync WSL2 clock after Windows sleep/hibernate drift.
# Runs outside the bwrap sandbox (called by dispatcher.py before spawning Claude).
# Requires sudoers entry — see wsl/SETUP.md for one-time setup.

set -euo pipefail

DRIFT_THRESHOLD=5  # seconds

# Query NTP drift without root using Python ntplib (pure-socket, no system writes)
check_drift() {
    python3 - <<'PYEOF' 2>/dev/null
import socket, struct, time, sys

# Minimal NTP query (RFC 5905)
NTP_SERVER = "pool.ntp.org"
NTP_PORT   = 123
EPOCH_DIFF = 2208988800  # NTP epoch (1900) vs Unix epoch (1970)

data = b'\x1b' + 47 * b'\0'
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)
    s.sendto(data, (NTP_SERVER, NTP_PORT))
    resp, _ = s.recvfrom(1024)
    s.close()
    t = struct.unpack('!12I', resp)[10]
    ntp_time = t - EPOCH_DIFF
    drift = abs(time.time() - ntp_time)
    print(f"{drift:.1f}")
except Exception:
    print("-1")
PYEOF
}

drift=$(check_drift)

if [[ "$drift" == "-1" ]]; then
    # NTP unreachable — sync anyway as a precaution
    :
elif python3 -c "exit(0 if float('${drift}') > ${DRIFT_THRESHOLD} else 1)" 2>/dev/null; then
    echo "[sync_clock] drift=${drift}s — syncing" >&2
else
    echo "[sync_clock] drift=${drift}s — within threshold, skipping" >&2
    exit 0
fi

# Try sync methods in order
if command -v chronyc &>/dev/null; then
    sudo chronyc -a makestep 2>/dev/null && echo "[sync_clock] synced via chronyc" >&2 && exit 0
fi

if command -v ntpdate &>/dev/null; then
    sudo ntpdate -s pool.ntp.org 2>/dev/null && echo "[sync_clock] synced via ntpdate" >&2 && exit 0
fi

sudo systemctl restart systemd-timesyncd 2>/dev/null && echo "[sync_clock] synced via systemd-timesyncd" >&2 && exit 0

echo "[sync_clock] WARNING: no sync method succeeded" >&2
exit 1
