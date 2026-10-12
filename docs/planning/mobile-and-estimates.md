# Phone companion and estimation learning: owner guide (M10)

## Phone companion (PWA)

The companion is the hub dashboard installed on your phone. With it you can talk to one of your PCs, follow jobs and approve or reject actions. It works on your home or office Wi-Fi (the hub is LAN-only).

1. **Trust the hub CA on the phone.** The file is `…\data\hub\ca.crt` on the hub PC; send it to yourself.
   - **iPhone/iPad:**
     1. Open the file to install the profile (Settings → Profile Downloaded → Install).
     2. Then go to Settings → General → About → **Certificate Trust Settings** and switch on full trust for "Panos hub CA".
   - **Android:** Settings → Security → Encryption & credentials → Install a certificate → **CA certificate**. Chrome trusts user-installed CAs; some other apps do not.
2. The phone must resolve the hub's name (for example `hubpc` or `hubpc.local`). If it doesn't, set a DNS name for the hub PC on your router.
3. Open `https://hubpc:48700/?app=companion` in Safari or Chrome and sign in:
   - with a new passkey: run `panos hub passkey-code` on the hub PC, then choose **Set up a passkey** on the phone;
   - or with a passkey you already set up on the PC, if your password manager syncs passkeys.
4. Install it:
   - Safari: Share → **Add to Home Screen**;
   - Chrome: menu → **Install app**.

   The app opens on **Talk**.

### What you can do

- **Talk:** pick a PC (and optionally a project) and write.
  - That PC answers with its own models and rules. A local-only project is answered by local models on that PC.
  - Requests like "list projects" or "research …" work as they do on the PC.
  - The hub keeps no conversation text, only who, when and which PC.
- **Approvals:** the badge on the tab (and on the app icon, where the phone supports it) shows how many approvals wait. Open one to see the exact action, then approve or reject it.
- **Jobs:** see live steps; pause, resume or cancel.

There are no system notifications: they would need Apple/Google push servers on the internet. The badge updates while the app is open.

The app caches only its own shell (HTML, scripts, icons), never your data.

## Estimates versus actuals

Panos compares what it estimated with what the work really took. Then it **suggests** better hours-per-point for you to approve.

1. **Keep a baseline** when you plan work: `panos effort baseline -p shop` (or **Effort → Save estimate as baseline** in the desktop app).
2. **Record actual effort:**
   - **Your hours:** `panos effort log US-003 2.5h -p shop` (add `--done` on the last entry when the story is finished).
   - **Tracker worklogs:** `panos effort import -p shop` reads Jira worklogs and Azure DevOps completed work of the work items linked to requirements. They arrive **pending**; check them with `panos effort list -p shop --status PENDING` and `panos effort approve|reject <id>`.
   - **Panos jobs** are recorded automatically (runtime, tokens, cost) as a separate machine figure. They never count as your hours.
3. **See how you did:** `panos effort report -p shop` shows estimated versus actual per finished story.
4. **Calibrate:** `panos effort suggest`. Once at least 5 finished stories have approved hours, Panos proposes new hours-per-point: overall, and per technology when there is enough data. It shows the bias (positive means estimates were too low) and the error. Approve with `panos effort calibration approve <id>`. From then on, estimates use it and say so ("calibrated from N finished stories …"). Nothing changes until you approve.

`estimation.calibration_min_stories` in `config.toml` sets the threshold (default 5).

## AI usage and cost

- `panos ai analytics [--days 30]`: calls, cost, local share, latency, budget burn, top models, projects and tasks.
- Desktop: Settings → AI analytics.
- Dashboard: **AI usage** shows every connected PC and the combined total.

## Live checks for the owner (M10 exit)

- [ ] Phone: install the companion, sign in with a passkey, and ask your PC "list projects".
- [ ] Phone: approve a job's action from the Approvals tab; the job continues on the PC.
- [ ] Log hours on five finished stories (or import from Jira/ADO and approve them), run `panos effort suggest`, approve, and see the new estimate.
