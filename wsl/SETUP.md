# WSL Environment Setup

Source code lives on Windows NTFS (this directory), accessible from WSL at:
    /mnt/<drive>/<path-to>/nanobot/wsl/

The Python venv must be created inside WSL's native filesystem to avoid
NTFS permission and performance issues. Do NOT create .venv here.

## First-time setup (run from inside WSL)

```bash
# 0. Install bubblewrap and allow passwordless clock sync (required once)
sudo apt install -y bubblewrap

# Replace YOUR_USERNAME with your actual Linux username
echo 'YOUR_USERNAME ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart systemd-timesyncd, /usr/sbin/ntpdate, /usr/bin/chronyc, /usr/sbin/chronyc' \
    | sudo tee /etc/sudoers.d/wsl-clock-sync > /dev/null
sudo chmod 440 /etc/sudoers.d/wsl-clock-sync

# 1. Install uv inside WSL if not already present
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Navigate to the source directory via the NTFS mount
cd /mnt/<drive>/<path-to>/nanobot/wsl

# 3. Create the venv in WSL's native filesystem
uv sync --python 3.11 --venv ~/venvs/nanobot

# 4. Install Playwright browsers
~/venvs/nanobot/bin/playwright install chromium
```

## Activating the venv (WSL)

```bash
source ~/venvs/nanobot/bin/activate
```

## Claude Code

Claude Code is invoked directly as a CLI command — it manages its own authentication
and does not require a Python venv. Ensure it is installed and authenticated:

```bash
claude --version
claude  # confirm auth is active
```

### Authentication — WSL is headless

**Critical:** WSL has no display server. When running `claude auth login`, the CLI cannot
open a browser automatically. Instead it prints a URL — open that URL manually in your
Windows browser, complete the OAuth flow on claude.ai, and the site will display a token
or code to paste back into the waiting WSL terminal.

If the WSL terminal is no longer available (e.g., session was closed), simply re-run
`claude auth login` in a fresh WSL terminal and repeat the process.

The bot's `wsl_auth.py` module handles proactive token refresh automatically using the
stored refresh token, so manual re-auth should only be needed when the refresh token
itself expires (typically weeks to months) or after an explicit logout. When that happens,
`/reauth` in Telegram will detect the failure and print a reminder of what to run.
