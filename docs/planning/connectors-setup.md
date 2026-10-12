# Connecting accounts: owner setup guide (M8)

Panos signs in with **your own** OAuth apps and tokens. Nothing goes through a Panos server. Every secret is stored in the OS keyring (Windows Credential Manager); the database only knows the account's name and what it may do. Everything below can also be done in the desktop app under **Settings → Connected accounts**.

New accounts are `LOCAL_ONLY`: their content is answered by local models only. To allow cloud models for one account, use `panos connect privacy <account> STANDARD` or tick "May be sent to cloud models" in Settings.

## Google (Gmail, Calendar, Drive)

1. Open <https://console.cloud.google.com/> and create a project (e.g. "Panos").
2. **APIs & Services → Library:** enable the *Gmail API*, *Google Calendar API* and *Google Drive API* (only those you need).
3. **OAuth consent screen:** User type *External*, publishing status *Testing*. Add yourself under *Test users*. In Testing mode, refresh tokens expire after 7 days, so you will reconnect weekly. Publishing the app for personal use avoids this, but Google then shows an "unverified app" warning.
4. **Credentials → Create credentials → OAuth client ID → Application type: Desktop app.** Copy the client ID and client secret. For a desktop client, the secret is not confidential in Google's model, but Panos still keeps it in the keyring.
5. Register the client and connect:

   ```
   panos connect client google --id <client-id>        # prompts for the secret (hidden)
   panos connect google -f mail -f calendar -f drive    # opens the browser for consent
   ```

Scopes requested, per feature:

| Feature | Scopes | What Panos does with them |
|---|---|---|
| mail | `gmail.readonly`, `gmail.compose` | Search, read, summarise; create/update **drafts only**. `gmail.compose` technically allows sending; Panos blocks it with an endpoint allowlist and has no send tool (BR-019). |
| calendar | `calendar.readonly`, `calendar.events` | Events, free/busy, conflicts. Create/update/delete/respond only after you approve the exact change. |
| drive | `drive.readonly` | Index folders you add as knowledge sources. |

To disconnect: `panos connect remove google`. This deletes the tokens and revokes the grant at Google.

## Microsoft 365 (OneDrive / SharePoint)

1. Open <https://entra.microsoft.com/> → **App registrations → New registration**. Name it "Panos". Supported account types: *Accounts in any organizational directory and personal Microsoft accounts*, or your tenant only.
2. **Authentication:** add a platform *Mobile and desktop applications* with the redirect URI `https://login.microsoftonline.com/common/oauth2/nativeclient`. Set **Allow public client flows = Yes** (needed for the device-code sign-in).
3. **API permissions → Microsoft Graph → Delegated:** `User.Read`, `Files.Read.All`, `Sites.Read.All`, `offline_access`. A work tenant may require admin consent.
4. Copy the *Application (client) ID* (and the *Directory (tenant) ID* if you restricted it to one tenant), then:

   ```
   panos connect client microsoft --id <client-id> [--tenant <tenant-id>]
   panos connect microsoft      # shows a code to enter at microsoft.com/devicelogin
   ```

5. Add a folder as knowledge: `panos knowledge add-onedrive hr <drive-id> --item <folder-item-id>`. Graph Explorer (`GET /me/drives`, `GET /sites/{site}/drives`) shows drive and item IDs.

## Jira Cloud

1. Create an API token at <https://id.atlassian.com/manage-profile/security/api-tokens>.
2. Connect: `panos connect jira acme.atlassian.net --email you@example.com` (prompts for the token, hidden).

Panos can search and read issues freely. Creating, updating, transitioning or commenting needs your approval of the exact change set. There is no delete.

## Azure DevOps

1. Open `https://dev.azure.com/<org>/_usersSettings/tokens` and create a personal access token with the scope **Work Items: Read & write**. Choose a short expiry.
2. Connect: `panos connect azure-devops https://dev.azure.com/<org>` (prompts for the PAT, hidden).
3. Searches need a project: `--project <name>`, or `"project"` in the tool input.

## Browser research

Research uses Playwright Chromium with its own profile (not your browser's):

```
uv sync                                  # installs the "browser" extra in development
uv run python -m playwright install chromium
panos research "how much does a 5 kW PV system produce in Crete?"
```

Settings (`config.toml`, `[browser]`): `search_url`, `max_sources`, `allow_private_networks` (default false), `headless`, `page_timeout_s`.

## Live checks for the owner (M8 exit)

- [ ] Google: connect mail + calendar; "έχω νέα μηνύματα;" lists unread mail; "τι έχω σήμερα;" shows today's events and conflicts; a draft appears in Gmail's Drafts folder and nothing is sent.
- [ ] Calendar change: ask for a new event; the approval shows the exact event; after approval it appears in Google Calendar without notifying attendees.
- [ ] Drive / OneDrive: add a folder as knowledge, index it, and find a document with `panos knowledge search`.
- [ ] Jira or ADO: search issues; approve a comment change set; the comment appears.
- [ ] Research: `panos research …` returns an answer with numbered sources.
