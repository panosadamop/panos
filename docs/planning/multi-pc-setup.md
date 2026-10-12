# Multi-PC setup: owner guide (M9)

One of your PCs becomes the **hub**. It keeps the list of your PCs, sends jobs to them, collects their approvals and serves the web dashboard. Every other PC (Windows, macOS or Linux) **joins** the hub. Everything stays on your LAN, and nothing listens on the internet (ADR-028/029).

A job sent to a PC runs on that PC, under that PC's own permissions and approvals. Project files are never copied between PCs.

## 1. Prepare the hub PC

1. Install Panos as usual, and Docker Desktop (for PostgreSQL).
2. Create the hub:

   ```
   panos hub init --hostname hubpc            # use this PC's LAN name; add --address 192.168.1.20 if it has a fixed IP
   docker compose -f "%LOCALAPPDATA%\Panos\Panos\data\hub\docker-compose.yml" up -d   # path is printed by init
   panos hub serve                            # keep it running (or start it at login)
   ```

   `init` creates a private CA, whose key is encrypted with a passphrase in the Windows Credential Manager. It also creates the hub certificate and a PostgreSQL configuration bound to `127.0.0.1` only. Note the **CA fingerprint** it prints.
3. **Firewall** (Windows): allow inbound TCP 48700 (dashboard) and 48701 (PC link) on **private** networks only:

   ```powershell
   New-NetFirewallRule -DisplayName "Panos hub" -Direction Inbound -Protocol TCP -LocalPort 48700,48701 -Profile Private -Action Allow
   ```

4. The hub's name must resolve on your LAN. Try `ping hubpc` (or `hubpc.local`) from another PC. If it does not resolve, add a line to that PC's hosts file, or set a DHCP reservation and DNS name on your router. The dashboard needs a **name**: passkeys do not work with bare IP addresses.

## 2. Join a PC

On the hub PC:

```
panos hub enroll-code --name laptop
```

This prints a one-time command (valid 10 minutes). Run it on the other PC, where Panos must be running:

```
panos node join https://hubpc:48700 --code XXXX-XXXX-… --fingerprint SHA256:…
panos node status        # "link connected"
```

You can also join from the desktop app: **Settings → Hub (other PCs)**. The fingerprint check means a device pretending to be your hub is refused. The PC's private key is kept in its OS keychain.

To remove a PC: `panos hub revoke laptop` (or **Revoke** in the dashboard). It is disconnected at once and needs a new code to come back. To leave from the PC itself: `panos node leave`.

## 3. Open the dashboard

1. **Trust the hub CA** in each device's browser store (once per device). The file is `…\data\hub\ca.crt` on the hub PC.
   - Windows: `certutil -user -addstore Root ca.crt`
   - macOS: `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ca.crt`
   - Linux (Chrome/Chromium): Settings → Privacy and security → Security → Manage certificates → Authorities → Import.
   - Firefox: Settings → Certificates → View Certificates → Authorities → Import.
2. On the hub PC run `panos hub passkey-code`. Then open `https://hubpc:48700`, choose **Set up a passkey**, enter the code and confirm with Windows Hello, Touch ID or your phone.
3. Next time, choose **Sign in with a passkey**. Add more passkeys (for example your phone) while signed in.

The dashboard shows:
- **PCs**: status, last seen, projects; add or revoke.
- **Jobs**: steps live from the PC; pause/resume/cancel.
- **Approvals**: the exact action; approve/reject.
- **Send a job**: the target PC is shown before sending.
- **AI usage**, **traceability**, and the hub's own hash-chained log.

For local-only projects, step details and approval summaries are shown live from the PC and are **not stored** on the hub.

A PC can refuse approvals from the hub: set `remote.accept_remote_approvals = false` in its `config.toml`.

## 4. Send a job from the hub PC's terminal

```
panos hub nodes
panos hub dispatch laptop "Run the test suite" -p shop --kind command    # or coding / research
panos hub status
```

## 5. Back up the hub

```
panos hub export-ca D:\Backup\panos-hub     # CA certificate + key encrypted with a password you choose
```

Losing the CA means every PC must join again. The PostgreSQL data lives in the Docker volume `panos_hub_pg`.

## Live checks for the owner (M9 exit)

- [ ] Hub on this PC; a **macOS or Linux** PC joins with `panos node join`; `panos hub nodes` shows it online with its projects.
- [ ] The dashboard opens over HTTPS without a certificate warning. You set up a passkey and sign in.
- [ ] Send a command job from the dashboard to the other PC. The approval appears in the dashboard; approve it; the job completes on that PC.
- [ ] Revoke the PC: `panos node status` on it shows "revoked by the hub".
- [ ] Stop the hub: the PCs keep working locally and reconnect when it is back.
