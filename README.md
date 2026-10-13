# Journal

A local, desktop-feeling markdown journal. Entries are plain markdown files
on disk at `data/<year>/<Month>/<DD-MM-YYYY>.md` — no database, no account,
no cloud, no CDN.

## Features

- **Plain files on disk** — every entry is an ordinary `.md` file organised
  by year and month. Back it up, sync it, or edit it with any editor.
- **Markdown editor with live preview** — a pinned toolbar (H1–H3, bold,
  italic, code, quote, list, link, code block, divider) inserts syntax at
  the cursor; syntax-coloured tokens; autosaves ~1s after you stop typing.
- **File tree** — year → month → file, mirroring the `data/` directory.
  "+ Today" / "+ Tomorrow" open that day's file, creating it (with a dated
  header) if it doesn't exist.
- **Mobile-friendly** — on narrow screens the editor takes the whole screen;
  the menu button opens the file tree as an overlay and the eye button opens
  the preview.
- **Light/dark theme** — toggle top-right, remembered across sessions.
- **Multi-device conflict detection** — each device tracks the version it
  loaded. If the file changed on disk since then (edited on another device),
  the next save is rejected (HTTP 409) and you choose which version wins.
- **Password-protected** — one shared password, stored as a salted scrypt
  hash (never plaintext); 30-day httpOnly session cookie.

## Setup

Requirements: **Python 3.10+**.

### Linux and macOS

```bash
git clone https://github.com/jonnie/journal
cd journal
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/set_password.py   # one-time: stores a salted scrypt hash in config.json
uvicorn app:app --port 8321
```

Open <http://127.0.0.1:8321> and enter the password.

### Windows

```powershell
git clone https://github.com/jonnie/journal
cd journal
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python scripts\set_password.py
uvicorn app:app --port 8321
```

Open <http://127.0.0.1:8321> and enter the password.

Notes:

- If `python` isn't on your PATH, use `py -3` (Windows) or your
  distribution's `python3`.
- Entries are created under `data/` next to the checkout; `config.json` and
  `data/` are gitignored, so cloning never touches existing entries.
- `uvicorn app:app` binds to `127.0.0.1` by default. To use the journal from
  other devices on your network, run with `--host 0.0.0.0` — but remember
  there is a single shared password and no TLS, so only do this on a network
  you trust.

## Optional: start automatically at login

The snippets below start the app whenever you log in. Replace the
`/path/to/journal` placeholders with the actual clone location (on Linux,
`%h` expands to your home directory).

### Linux (systemd user unit, no sudo)

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/journal.service <<'EOF'
[Unit]
Description=Journal — markdown journaling web app

[Service]
Type=simple
WorkingDirectory=%h/path/to/journal
ExecStart=%h/path/to/journal/.venv/bin/uvicorn app:app --port 8321
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now journal
```

To keep it running after you log out (start at boot with no login session),
enable lingering — the only sudo step: `sudo loginctl enable-linger $USER`.

Managing:

```bash
systemctl --user status journal     # is it running?
journalctl --user -u journal -f     # follow logs
systemctl --user restart journal    # after changing code
```

### macOS (launchd LaunchAgent)

```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/com.jonnie.journal.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.jonnie.journal</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/journal/.venv/bin/uvicorn</string>
        <string>app:app</string>
        <string>--port</string>
        <string>8321</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/journal</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
EOF
launchctl load ~/Library/LaunchAgents/com.jonnie.journal.plist
```

Unload with `launchctl unload ~/Library/LaunchAgents/com.jonnie.journal.plist`.

### Windows (Task Scheduler)

```powershell
$action = New-ScheduledTaskAction `
    -Execute "C:\path\to\journal\.venv\Scripts\pythonw.exe" `
    -Argument "-m uvicorn app:app --port 8321" `
    -WorkingDirectory "C:\path\to\journal"
Register-ScheduledTask -TaskName "Journal" `
    -Action $action -Trigger (New-ScheduledTaskTrigger -AtLogOn)
```

Remove it with `Unregister-ScheduledTask -TaskName "Journal" -Confirm:$false`.

Don't run a manual `uvicorn` on port 8321 while the service runs.

## Notes

- `config.json` holds the password hash and a session-signing secret —
  never plaintext; it's gitignored and should stay that way.
  `python scripts/set_password.py` takes effect immediately — config is read
  per request, no restart needed.
- Sessions last 30 days (httpOnly cookie). `POST /logout` signs out.
- Dependencies: `fastapi`, `uvicorn` (see `requirements.txt`).
  `marked.min.js` is vendored locally — no CDN needed at runtime.
- Your journal is only as safe as the machine it lives on: it's plain
  markdown on disk. Back up `data/` if the entries matter to you.

## License

[MIT](LICENSE).
