# Google setup: give curldex access to your Google account

curldex's Google apps, `google_calendar` and any Google API app you add (Gmail, Drive...), call
Google's APIs as you. Google has no personal access token you could copy, so this takes a one-time
setup in the Google Cloud console, about ten minutes. Claude Code takes you through it.

## Set it up with Claude Code

1. **Start Claude Code in the curldex folder** (your clone):
   ```bash
   cd curldex
   claude
   ```
2. **Give it this request:**
   ```text
   Set up Google access for curldex's google_calendar app, following docs/google_setup.md.
   I have no Google Cloud project yet.
   - Take me through the Google Cloud console one step at a time, and wait for me after each.
   - If the gcloud command is installed and logged in, use it for the steps it can do; ask me first.
   - Tell me what to put in credentials.yaml, but never ask me for the client secret.
   - Then run the login with me, and check it with a read call.
   ```
   Change it to fit:
   - **A project you already have:** replace the second line with *I already have a Google Cloud
     project, `<its name>`: check it against the checklist, and fix only what's missing.*
   - **Another Google app:** name it instead of `google_calendar`. The same project and OAuth client
     serve every Google app; a new one only needs its API enabled, and a login of its own.
   - **No browser login** (a server, say): add *Use a service account* (see
     [A service account instead](#a-service-account-instead)).
3. **Do your part as Claude asks.** Claude can't sign in to Google as you, so three things are yours:
   - **The clicks in the console**, in your browser, signed in with the Google account whose data
     you want to use. Claude says where to click and what to type, and adapts when the console looks
     different. If Google shows an error, such as `access_denied` or `redirect_uri_mismatch`, tell
     Claude: it knows which setting each one points at.
   - **The client ID and secret,** which you put in `credentials.yaml` yourself, in your editor.
     Never paste the secret into the chat: Claude doesn't ask for it, and is denied reading that file.
   - **Allowing access** when Claude runs the login: open the URL it gives you, allow access, and
     paste back the whole address the browser lands on. That address holds a one-time code, not your
     password.
4. **You're done** when Claude's read call works: it shows your time zone. In IPython,
   `curldex.load_app("google_calendar").status()` then says `ready`.

The README's [quick start](../README.md#3-google-calendar-with-your-account) goes on from there.

## What the setup does

What Claude takes you through, and why each piece is needed, so that you know what you're setting
up. You don't follow it by hand.

There are two ways in:

| | OAuth login (the usual way) | A service account |
|---|---|---|
| curldex acts as | you | a robot account you share data with |
| Setup | an OAuth client, then one login in the browser | a key file; you share your calendar with it |
| Expires | every 7 days while the consent screen is in Testing ([below](#when-the-login-expires)) | never |

Either way, the values end up in the app's `config/credentials.yaml`. That file is git-ignored, and
Claude Code is denied reading it (`.claude/settings.json`).

### The OAuth login

The setup makes these pieces, in this order:

1. **A Google Cloud project,** at https://console.cloud.google.com/: the container for everything
   below, under any name (`curldex`, say). One project serves every Google app in curldex.
2. **The API, enabled:** in a new project every Google API is off. `google_calendar` needs the
   *Google Calendar API* (`calendar-json.googleapis.com`; from the command line,
   `gcloud services enable calendar-json.googleapis.com`); a Gmail app needs the *Gmail API*, and so on.
3. **The consent screen** (*Google Auth Platform* in the console): what Google shows when you allow
   access, such as "curldex wants to access your Google Account". Its audience is **External**, so
   that any Google account can use it, a personal one too. Its status stays **Testing**, with your
   own address as a **test user**: then only you can log in, and Google ends each login after 7 days.
4. **An OAuth client of type *Desktop app*:** a client ID (ending in `.apps.googleusercontent.com`)
   and a client secret, which tell Google that the login is for curldex. *Desktop app* lets the login
   come back to curldex on your own computer, at `http://127.0.0.1:PORT/`. A *Web application* client
   refuses that, with `redirect_uri_mismatch`.
5. **`credentials.yaml`:** the client ID and secret go in the app's
   `curldex/apps/google_calendar/config/credentials.yaml`. It's a copy of `credentials.example.yaml`,
   next to it, made readable by you only (`chmod 600`).
6. **The login, once:** `cal.login()` in IPython, or `curldex login google_calendar`, opens Google's
   sign-in page and asks for the scopes in the app's `api.yaml` (for `google_calendar`, reading and
   changing your calendars).
   - Google warns that it *hasn't verified this app*. The app is your own client, so you continue
     and allow.
   - The browser then goes to `http://127.0.0.1:PORT/?code=...`, which curldex receives. When the
     browser runs on another machine, the page says it can't connect, and you paste that address
     into IPython instead.
   - When Claude runs the login, it's two steps: `curldex login google_calendar --start` prints the
     URL, and `--finish "<address>"` completes it with the address you paste back.

   curldex saves the tokens in `config/.state.json`, and refreshes the hour-long access token by
   itself from then on.

A read call checks the result: `cal.run("get-setting", setting="timezone")["value"]` shows your time zone.

### Checklist

What ends up set in Google Cloud. Claude checks an existing project against it.

| Where in the console | Setting | Value |
|---|---|---|
| project picker | the project | any name, such as `curldex`; its number starts your client ID |
| APIs & Services > Library | the API | *Google Calendar API* enabled (*Gmail API*... for other apps) |
| Google Auth Platform > Branding | App name | `curldex`: Google's sign-in page shows this name |
| Google Auth Platform > Branding | support and developer contact emails | your address |
| Google Auth Platform > Audience | User type | **External** |
| Google Auth Platform > Audience | Publishing status | **Testing** (logins last 7 days) or **In production** ([below](#when-the-login-expires)) |
| Google Auth Platform > Audience | Test users | your address (needed while in Testing) |
| Google Auth Platform > Clients | the OAuth client | type **Desktop app** |
| Google Auth Platform > Data access | scopes | nothing to add: curldex asks for `auth.scopes` from `api.yaml` at login |

### When the login expires

While the consent screen is in **Testing**, Google ends every login after 7 days. Commands then
fail with `the login expired or was revoked; run curldex login google_calendar`. `status()` says
`ready` until a call asks Google, as it can't tell before, and `login required` after. Logging in
again fixes it (`cal.login()`); the client ID and secret stay as they are.

To end the limit for good, publish the app: ask Claude, or *Google Auth Platform > Audience >
Publish app > Confirm*, then log in once more (logins from the Testing period keep their 7-day
limit).
- No verification by Google is needed for your own use; the *unverified app* warning stays.
- A published, unverified app takes up to 100 Google accounts, and the test-user list no longer
  applies.
- Its login still ends if you remove its access, if it goes unused for 6 months, or after about 100
  logins with the same client (Google drops the oldest). Then log in again.

### A service account instead

A service account is a robot account in your Google Cloud project. curldex signs in as it with a key
file, so there is no browser login and nothing expires: the choice for servers and automation. It
acts as itself, not as you, so it sees only what you share with it.

Setting one up, instead of the OAuth client and login, involves:
- the project and the API, as in the OAuth login, steps 1 and 2;
- the account itself (*IAM & Admin > Service accounts*), with no roles, and a JSON key for it, saved
  as the app's `config/service_account.json`: git-ignored, left out of builds, and denied to Claude
  like `credentials.yaml`. Some organizations turn off key creation; the OAuth login is the way in then;
- sharing your calendar with the account's email address (`...@....iam.gserviceaccount.com`),
  with *Make changes to events*;
- in `credentials.yaml`: `service_account_file: service_account.json` (a relative path is read from
  the config folder), and `calendar_id`, your address.
  `primary` would be the robot's own empty calendar.

Gmail and Chat can't be shared like a calendar: a Google Workspace admin grants the account
domain-wide delegation, and `service_account_subject` names the user it acts as.

With a service account, `cal.login()` says *no login needed*; the read call above checks it.

### Logging out

To log out, delete the app's `config/.state.json` and `config/.curlrc` (the last access token). To
take back the access itself: https://myaccount.google.com/permissions > the app's name > **Remove access**.
