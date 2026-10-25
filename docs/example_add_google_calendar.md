# Example: build an app with Claude Code, and use it from IPython

This walks through making a curldex app with Claude Code, from the request to the first calls in
IPython. The example is Google Calendar. curldex ships it as `google_calendar`, so you move that app
aside and have Claude build it again, from nothing, against your own calendar. Any other service
goes the same way; only the Google login is specific to Google.

You need curldex installed ([README](../README.md#1-install), step 1), Claude Code, `jq` and a
Google account. Commands run in the curldex folder, your clone: in a shell, or in IPython (started
there with `uv run ipython`), where a line starting with `!` runs in the shell.

## 1. Move the shipped app aside

```bash
mv curldex/apps/google_calendar ~/google_calendar.shipped
```

Outside the curldex folder, `curldex apps` no longer lists it, and Claude doesn't find it to copy
from, so it starts from nothing. If you had set up its Google access, your `credentials.yaml` and
login moved with it; the client ID and secret in that `credentials.yaml` work for the new app too.
Section 5 puts the shipped app back.

`.claude/settings.json` only denies Claude reading credential files under `curldex/apps/`, so the
parked copy's `credentials.yaml` and `.state.json` are outside that guard. Park it somewhere a rule
already covers, or add one to `.claude/settings.local.json` (git-ignored):
`Read(~/google_calendar.shipped/**)`.

## 2. Ask Claude Code for the app

1. **Start Claude Code in the curldex folder,** so that it loads the skill, `CLAUDE.md` and
   `.claude/settings.json`:
   ```bash
   cd curldex
   claude
   ```
2. **Give it this request:**
   ```text
   /curldex-new-app add an app google_calendar for Google Calendar. I want to list, add and
   delete events from IPython. Add the helpers events_on(day), one day's events in my time zone,
   and add_event(summary, start, minutes). This is a practice rebuild of the shipped app, which I
   moved aside: change nothing outside curldex/apps/google_calendar/.
   ```
   `/curldex-new-app` starts the skill that ships with curldex,
   [.claude/skills/curldex-new-app/SKILL.md](../.claude/skills/curldex-new-app/SKILL.md): the
   workflow Claude follows (section 3). A plain request, such as "add a Google Calendar app to
   curldex", usually starts it too.
3. **Answer its questions,** such as which tasks you need, and whether you use an OAuth client or a
   service account.
4. **Do your part when Claude asks.** Claude does the rest.
   - **The credentials:** copy the template Claude wrote, then fill in the copy in your editor.
     ```python
     In [1]: !cp curldex/apps/google_calendar/config/credentials.example.yaml curldex/apps/google_calendar/config/credentials.yaml
     In [2]: !chmod 600 curldex/apps/google_calendar/config/credentials.yaml
     ```
     If you set up the shipped app, the client ID and secret are in
     `~/google_calendar.shipped/config/credentials.yaml`. If you have none yet, Claude first takes
     you through the Google setup ([google_setup.md](google_setup.md)). Never paste the secret into
     the chat: Claude doesn't ask for it, and is denied reading the file.
   - **The login:** open the URL Claude gives you, click **Continue** on Google's *unverified app*
     page, then **Allow**, and paste back the whole address the browser lands on (the page may say
     it can't connect; that's expected). Or log in yourself, in IPython:
     `curldex.load_app("google_calendar").login()`.
   - **The tests that write:** Claude runs the read APIs by itself. It creates, changes or deletes
     an event only with your go-ahead, and only a test event it made: the app acts on your real
     calendar.
5. **Read Claude's summary:** what the app covers, what's verified, and a few commands to try.

## 3. What Claude does

What the skill has Claude do, and why, so that you can follow along. You don't do these steps.

1. **Finds where the API is described.** Google publishes a machine-readable description of each of
   its APIs, a *Discovery document* (Google's own client libraries are generated from it), and lists
   them in a directory (`https://www.googleapis.com/discovery/v1/apis?name=calendar`). Calendar's is at:
   ```
   https://www.googleapis.com/discovery/v1/apis/calendar/v3/rest
   ```
   It describes the Calendar API's 38 methods, in 8 groups (`acl`, `calendarList`, `calendars`,
   `channels`, `colors`, `events`, `freebusy`, `settings`): each one's HTTP method, path and params,
   the OAuth scopes that allow it, and the shape of its body. `events.list`, shortened:
   ```json
   "list": {
     "httpMethod": "GET",
     "path": "calendars/{calendarId}/events",
     "parameters": {
       "calendarId": {"type": "string", "location": "path", "required": true, "description": "Calendar identifier. ..."},
       "maxResults": {"type": "integer", "location": "query", "description": "Maximum number of events returned on one result page. ..."},
       ...
     },
     "scopes": ["https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/calendar.readonly", ...]
   }
   ```
2. **Writes a starter app.** `__init__.py` holds a class that subclasses `GoogleApp`, which gives
   the app Google's login and tells curldex that the description is a Discovery document
   (`curldex/shared/google.py` reads it). `config/api.yaml` names the document in `source:`, with
   the scope the app asks for at login:
   ```yaml
   description: Google Calendar - calendars and events
   base_url: https://www.googleapis.com/calendar/v3
   auth:
     type: oauth2
     scopes: [https://www.googleapis.com/auth/calendar]
   source:
     url: https://www.googleapis.com/discovery/v1/apis/calendar/v3/rest
   commands:
   ```
3. **Lists the APIs:** `curldex discover google_calendar` fetches the document with curl, and lists
   each method as an api.yaml entry would name it: `events.list` becomes
   `events-list  GET  /calendars/{calendarId}/events`. It leaves out, and names, the APIs the scopes
   don't allow.
4. **Picks the ones your tasks need.** It leaves out dangerous ones in `source:`, such as
   `exclude: [calendars-clear]` (it deletes every event in a calendar), then
   `curldex discover google_calendar --add 'events-*' calendar-list-list` appends the chosen ones to
   api.yaml, keeping Google's names:
   ```yaml
     events-list:
       method: GET
       path: /calendars/{calendarId}/events
       description: Returns events on the specified calendar.
       params:
         calendarId:
           in: path
           required: true
           description: Calendar identifier.
         maxResults:
           in: query
           type: integer
           description: Maximum number of events returned on one result page.
         ...
   ```
5. **Finishes each API:** a one-line description, defaults (such as your calendar, from
   `credentials.yaml`), the right access level, and an `example:` that `curldex verify` can run. The
   Discovery document has no examples, and no advice on which APIs are safe: those are Claude's part.
6. **Writes `credentials.example.yaml` and the helpers,** then runs `curldex check google_calendar`
   until it reports no errors.
7. **Verifies the APIs against your calendar,** once you've filled in the credentials and logged in:
   the read APIs by themselves, the writes on a test event it creates and then deletes. The results
   go into `config/verified.yaml`: the date, the HTTP status and the response's shape, no data.
8. **Writes the docs:** `curldex docs google_calendar` generates `API.md`, the app's full reference.

To look at the Discovery document yourself, in IPython:

```python
In [1]: !curl -sS 'https://www.googleapis.com/discovery/v1/apis/calendar/v3/rest' | jq '[.resources[].methods | length] | add'   # 38
In [2]: !curl -sS 'https://www.googleapis.com/discovery/v1/apis/calendar/v3/rest' | jq '.resources.events.methods.list'
```

## 4. Use it from IPython

In a new IPython, so that it loads the app as Claude left it. The API names are Google's own, which
the skill has Claude keep; `cal.apis()` lists them.

```python
In [1]: import curldex
In [2]: cal = curldex.load_app("google_calendar")
In [3]: cal.status()                               # 'ready', or what's missing
In [4]: cal.apis()                                 # the APIs, with read / write / destructive
In [5]: cal.help("events-insert")                  # params and an example

# the helpers you asked for
In [6]: [(e.get("summary"), e["start"]) for e in cal.events_on()]          # today's events
In [7]: from datetime import datetime
In [8]: ev = cal.add_event("Dentist", datetime(2026, 11, 2, 15, 0).astimezone(), minutes=45)
In [9]: ev["htmlLink"]                             # the event in the Calendar web app

# any API: run() returns the JSON response
In [10]: now = datetime.now().astimezone().isoformat(timespec="seconds")
In [11]: cal.run("events-list", calendarId="primary", timeMin=now, singleEvents=True, orderBy="startTime",
    ...:         maxResults=5)
In [12]: cal.run("events-delete", calendarId="primary", eventId=ev["id"])  # destructive: asks first

# or the curl command, run in the shell
In [13]: cmd = cal.curl("events-list", calendarId="primary", timeMin=now, maxResults=5)
In [14]: cmd        # curl -q -sS -g -K "$(curldex auth google_calendar)" 'https://www.googleapis.com/calendar/v3/calendars/primary/events?maxResults=5&timeMin=...'
In [15]: !{cmd} | jq '.items[].summary'           # your next five events (nothing if none)
```

- A destructive API asks before it runs, unless you pass `yes=True`.
- `primary` is your main calendar. Another calendar's ID is in Google Calendar > Settings > the
  calendar > *Integrate calendar*.
- Without `timeMin`, `events-list` starts at your calendar's oldest events, so give it one to see
  what's coming up.
- While the consent screen is in Testing, Google ends the login after 7 days: the next call stops
  with `the login expired or was revoked`. Run `cal.login()` again
  ([google_setup.md](google_setup.md#when-the-login-expires)).
- If Claude changes the app later, `cal.reload()` rereads `api.yaml`; after a change to
  `__init__.py`, start a new IPython.
- IPython reads `{...}` after `!` as Python: `!{cmd}` needs nothing more, but a literal brace in a
  command you type is doubled (`-w '%{{http_code}}'`).

## 5. Put the shipped app back

```bash
mv curldex/apps/google_calendar ~/google_calendar.rebuilt      # keep the rebuilt app, or delete it
mv ~/google_calendar.shipped curldex/apps/google_calendar
```

The README's quick start, `docs/google_setup.md`'s check, `demo/google_calendar.ipy` and the tests use
the shipped app and its API names (`list-events`, `get-setting`), so put it back before using them. Until then, `git status` shows the shipped
app as deleted: don't commit while it's moved aside.

## Another example: an API described in a file

A service may have no published description at a URL, only an OpenAPI file, such as an internal lab
testbed's. Claude makes the app from the file the same way: `source:` names the file instead of a
URL. OpenAPI is curldex's default format, so the app needs no class (only an `__init__.py` for any
helpers you ask for).

1. **Put the file in the app's config folder,** or give Claude its path and it copies it there:
   ```python
   In [1]: !mkdir -p curldex/apps/testbed/config
   In [2]: !cp /path/to/testbed-openapi.yaml curldex/apps/testbed/config/openapi.yaml
   ```
2. **Ask for the app:**
   ```text
   /curldex-new-app add an app testbed for our lab testbed at https://testbed.example.com.
   Its API is described in curldex/apps/testbed/config/openapi.yaml (OpenAPI). I need to list
   devices and make reservations. It takes a bearer token.
   ```

What Claude does then:

1. **Starts api.yaml with the file as `source:`** (a path is relative to the config folder):
   ```yaml
   source:
     url: openapi.yaml
   commands:
   ```
2. **Lists the APIs:** `curldex discover testbed` reads the file and names each API from its
   `operationId` (`listDevices` becomes `list-devices`). As api.yaml has no description, base_url or
   auth yet, it also prints what the file suggests for them:
   ```
   api.yaml has no description, base_url, auth. The description suggests (check, then paste into api.yaml):

   description: Reserve and control lab devices.
   base_url: /api/v2
   auth:
     type: bearer

   testbed: 5 of 5 APIs in the description aren't in api.yaml
     create-reservation  POST   /reservations                Reserve a device.
     delete-reservation  DELETE /reservations/{id}           Cancel a reservation.
     get-device          GET    /devices/{device_id}         Get one device.
     list-devices        GET    /devices                     List devices.
     reboot-device       POST   /devices/{device_id}/reboot  Reboot a device.
   Add them with: curldex discover testbed --add NAME_OR_GLOB ...
   ```
3. **Checks the suggestions against the real testbed,** never pasting them unchecked, picks the
   APIs, and runs `curldex discover testbed --add '*'`:
   ```yaml
   description: Lab testbed - devices and reservations
   base_url: https://${testbed_host}/api/v2       # the file's servers: gives only /api/v2
   auth: {type: bearer}
   source:
     url: openapi.yaml
     exclude: [reboot-device]                     # not needed, and it disrupts other users
   commands:
   ```
4. **Goes on as for Calendar** (section 3, steps 5 to 8): `credentials.example.yaml` (here
   `testbed_host` and `token`), an example for each API, and `curldex check testbed`. You fill in
   `credentials.yaml`, and Claude runs `curldex verify testbed`.

What to watch for in a testbed's own file:

| In the file | What happens | Fix |
|---|---|---|
| Swagger 2.0 (`swagger: "2.0"`) | an error: only OpenAPI 3 is read | convert it first: `source: {url: "https://converter.swagger.io/api/convert?url=<its URL>"}` |
| Split over several files (`$ref: schemas/device.yaml`) | only refs inside the file (`#/components/...`) are followed: params, bodies and even whole APIs kept in the other files are dropped **without a warning** | bundle it into one file first: `npx @redocly/cli bundle openapi.yaml -o bundled.yaml` |
| A relative `servers:` (`/api/v2`), or none | the suggested base_url is relative | a full URL; for a host that differs per lab, `https://${testbed_host}/api/v2` with `testbed_host` in credentials.yaml |
| No auth, or auth that differs from the testbed's | it suggests `type: none`, or what `securitySchemes` says | Claude sets the auth the testbed really takes |
| A self-signed TLS certificate (the testbed's, not the file's) | curl refuses to connect | `curl_options: [--cacert, /path/to/testbed-ca.pem]` in api.yaml |
| Out of date with the testbed | wrong paths or params | `curldex verify` shows which; Claude fixes those entries by hand |
