# curldex

[![CI](https://github.com/ytan88/curldex/actions/workflows/ci.yml/badge.svg)](https://github.com/ytan88/curldex/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

A Python toolbox that finds, documents and runs the right curl command for any web API.

- Each web service is a folder, `curldex/apps/<app>/`: its APIs in a config file
  (`config/api.yaml`, with an example for each), its login, and its documentation. A service that
  needs code (helpers, an unusual login) is also a Python package; most need none.
- For a service with a browser login, you log in once; curldex refreshes the login after that.
- Then you work with the service through plain curl commands, in IPython, and so can Claude.
  curldex lists the APIs and explains each one with an example curl command; curl does the HTTP.
- **Need a service that isn't there yet?** Ask Claude Code, in this project, to add it
  ([step by step](#add-a-web-service-with-claude-code)). The skill `.claude/skills/curldex-new-app/`
  tells it how: it researches the API, writes the app, checks it, and verifies it against the real
  service.

No command ever holds a secret: curl reads the credentials from a file that `curldex auth <app>`
writes and prints the path of.

```python
In [1]: import curldex
In [2]: cal = curldex.load_app("google_calendar")
In [3]: cal.curl("list-calendars")
Out[3]: curl -q -sS -g -K "$(curldex auth google_calendar)" 'https://www.googleapis.com/calendar/v3/users/me/calendarList'
```

## Quick start

After installing, the steps run in IPython, started in the curldex folder (the Google setup runs in
Claude Code).

### 1. Install

You need Linux or macOS, Python 3.10 or newer, [uv](https://docs.astral.sh/uv/), curl 7.87 or newer,
`jq` for the examples, and [Claude Code](https://claude.com/claude-code) to set up Google access and
to add services.

```bash
git clone https://github.com/ytan88/curldex.git
cd curldex
uv sync                     # .venv: curldex (editable), PyYAML, and the dev tools IPython, pytest, ruff
uv run pytest -q            # optional: the offline tests, a few seconds; everything should pass
uv run ipython              # IPython in the project's environment: the steps below are typed here
```

Start IPython with `uv run ipython` each time: it puts `.venv/bin` on the PATH, so `!curldex ...`
and the curl commands curldex builds find the `curldex` command.

curldex runs from this clone, installed editable by `uv sync`, rather than from `pip install`: each
app keeps your credentials and tokens in its own folder, `curldex/apps/<app>/config/`, so that folder
has to be yours to write in.

### 2. First calls: httpbin, no account needed

httpbin.org answers every request with what it received, so it needs no setup:

```python
In [1]: import curldex
In [2]: !curldex apps                                  # the apps in your copy, and whether each is ready
In [3]: hb = curldex.load_app("httpbin")
In [4]: hb.apis()                                      # its APIs: name, access level, method, path
In [5]: hb.help("get-query")                           # one API: its params and an example curl command
In [6]: cmd = hb.curl("get-query", q="hello", limit=3)
In [7]: cmd                                            # the command, as text
Out[7]: curl -q -sS -g --compressed -H 'Accept: application/json' 'https://httpbin.org/get?q=hello&limit=3'
In [8]: !{cmd} | jq .args                              # run it
{
  "limit": "3",
  "q": "hello"
}
In [9]: hb.run("get-query", q="hello")["args"]         # or run it from Python: the parsed JSON
Out[9]: {'limit': '10', 'q': 'hello'}
```

`limit` came back as `10` in `Out[9]`: `help()` lists each param's default, and `curl()` and `run()`
fill it in.

In IPython, a line starting with `!` runs in the shell, and `{cmd}` after `!` puts in a Python value.
`!{cmd}` needs no escaping, but any other brace on that line is doubled: in a jq filter
(`!{cmd} | jq '{{id, summary}}'`), or in a command you type yourself
(`!curl -sS -o /dev/null -w '%{{http_code}}' https://httpbin.org/get` prints `200`). With a single
brace, IPython fills in nothing on the line, not even `{cmd}`.

### 3. Google Calendar, with your account

1. **Set up Google access, once,** with Claude Code (about ten minutes): give it the request in
   [docs/google_setup.md](docs/google_setup.md#set-it-up-with-claude-code). It takes you through the
   Google Cloud console, has you put the client ID and secret in `credentials.yaml`, and runs the
   login with you.
2. **Check it in IPython:**
   ```python
   In [10]: cal = curldex.load_app("google_calendar")
   In [11]: cal.status()
   Out[11]: 'ready'
   ```
   Until the setup is done, `status()` says what's still missing, first
   `missing client_id, client_secret in .../config/credentials.yaml (copy credentials.example.yaml, next to it, to start it)`,
   then `login required: run curldex login google_calendar (in Python: app.login())`. It reads the
   files each time, so the app needs no reloading. Before the login, the app's commands stop with
   `curldex: google_calendar: login required: ...`, followed by curl's
   `option -K: error encountered when reading a file`: curl got no credentials.

   While the consent screen is in *Testing*, Google ends the login after 7 days: a call then stops
   with `the login expired or was revoked`, and `cal.login()` logs in again
   ([When the login expires](docs/google_setup.md#when-the-login-expires)). `login()` prints a URL,
   opens it, and waits for the browser to come back to `http://127.0.0.1:PORT/`. If the browser is
   on another machine, or says it can't connect, paste the whole address from its address bar into
   IPython, where `login()` is waiting.
3. **Work with your calendar:**
   ```python
   In [12]: cal.apis()                                       # the Calendar APIs
   In [13]: cal.help("list-events")                          # params, and the command with your values
   In [14]: from datetime import datetime
   In [15]: now = datetime.now().astimezone().isoformat(timespec="seconds")
   In [16]: cmd = cal.curl("list-events", timeMin=now, maxResults=3)
   In [17]: !{cmd} | jq '.items[].summary'                   # your next three events (nothing if none)
   In [18]: [e.get("summary") for e in cal.events_on()]      # a helper the app adds: today's events
   ```

[demo/google_calendar.ipy](demo/google_calendar.ipy) goes further: it creates a test event, moves
it, and deletes it, with curl only (`uv run ipython demo/google_calendar.ipy`). Or ask Claude Code,
started in this folder: *"what's on my calendar tomorrow?"*

Then add the services you need: next section.

## Add a web service with Claude Code

Any web service, website or API: Jira, Notion, GitHub, an internal test site behind a username and
password... You describe it; Claude writes the app; you supply the credential and approve the tests.

Claude Code works in one terminal. Check each result in IPython in another (`uv run ipython`, in
the curldex folder). The example below adds Jira Cloud, as the app `jira_cloud`; use the app name
Claude chooses, and the API names `app.apis()` shows.

1. **Start Claude Code in the curldex folder** (`cd curldex && claude`). Starting there loads
   `CLAUDE.md`, the skill `.claude/skills/curldex-new-app/` and `.claude/settings.json`; Claude
   Code asks once whether to trust the folder.
2. **Ask for the service and say what you want to do with it:**
   > add Jira Cloud: I need to search issues and add comments

   A request like that starts the skill; `/curldex-new-app` starts it directly.
3. **Answer its questions:** which edition (cloud or self-hosted, the URL), which tasks, which
   credential you have or can get (a token, an API key, a username and password, an OAuth app).
   Never paste a secret's value into the chat; Claude doesn't ask for one.
4. **Claude researches and writes** `curldex/apps/jira_cloud/`: `config/api.yaml` (the APIs, each
   with an example), `config/credentials.example.yaml` (each credential, and where to get it), and
   Python only if the service needs helpers or an unusual login. It runs `curldex check` until the
   app is clean. Look at what it wrote:
   ```python
   In [1]: import curldex
   In [2]: app = curldex.load_app("jira_cloud")
   In [3]: app.apis()                     # the APIs it added, with their access levels
   In [4]: app.status()                   # what's missing: the credentials, at first
   Out[4]: 'missing email, api_token in .../config/credentials.yaml (copy credentials.example.yaml, next to it, to start it)'
   ```
   If Claude changes the app's Python code later, start a new IPython (or run
   `%load_ext autoreload` and `%autoreload 2` first); after a change to `api.yaml` alone,
   `app.reload()` is enough.
5. **Fill in the credentials yourself** when Claude asks: copy the template and edit the copy.
   ```python
   In [5]: !cp curldex/apps/jira_cloud/config/credentials.example.yaml curldex/apps/jira_cloud/config/credentials.yaml
   In [6]: !chmod 600 curldex/apps/jira_cloud/config/credentials.yaml
   ```
   Open the copy in your editor; the template's comments say where each value comes from. Claude
   is denied reading this file. Then:
   ```python
   In [7]: app.status()                   # 'ready', or 'login required: ...' for an OAuth service
   ```
6. **Log in, for an OAuth service:**
   ```python
   In [8]: app.login()                    # prints a URL and opens it: sign in and allow access
   In [9]: app.status()
   Out[9]: 'ready'
   ```
   A Google API also needs that API enabled in your Google Cloud project; Claude takes you through
   it ([docs/google_setup.md](docs/google_setup.md)). Claude can run the login for you instead:
   it gives you a URL; open it, allow access, and paste back the address the browser lands on.
7. **Verify the examples against the real service.** Read APIs run freely; writes are skipped:
   ```python
   In [10]: app.verify()
   get-current-user: verified on 2026-10-24 (HTTP 200)  {accountId: string, displayName: string, ...}
   add-comment: skipped, a write API (verify it by itself, on test data, with yes=True or --yes)
   ```
   Run a write or destructive API by itself, only on data made for the test, with values that point
   at it:
   ```python
   In [11]: app.verify("add-comment", yes=True, issueIdOrKey="TEST-1")
   ```
   Each result goes into `config/verified.yaml` (date, HTTP status and the response's shape, no
   data) and `API.md`. A value you pass, such as `TEST-1`, is recorded as a stand-in, such as
   `<issueIdOrKey>`, so the file can be shared. Claude does this step itself if you let it: reads by
   itself, writes only with your go-ahead and only on data it creates for the test and then deletes.
8. **Use it:**
   ```python
   In [12]: app.help("get-current-user")   # params, the command, and when it was verified
   In [13]: cmd = app.curl("get-current-user")
   In [14]: !{cmd} | jq .displayName
   ```
   The full reference, every API with its example and the Authentication section, is
   `curldex/apps/jira_cloud/API.md`.
9. **Commit the app folder.** `.gitignore` keeps `credentials.yaml`, `.state.json` and `.curlrc`
   out. A service that shouldn't go in this repo (company-internal, say): ask Claude to write it in
   another folder, and tell curldex where it is before loading it:
   ```python
   In [15]: %env CURLDEX_APPS=/path/to/my_apps
   In [16]: app = curldex.load_app("internal_wiki")
   ```
   Several folders are separated by `:`. To set it for good, export `CURLDEX_APPS` in your shell
   before `uv run ipython`. `.claude/settings.json` denies Claude reading the credential files only
   under `curldex/apps/`, so copy its `deny` rules into `.claude/settings.local.json` (git-ignored),
   with `./curldex/apps/` changed to your folder (`//` starts an absolute path, as in
   `Read(//path/to/my_apps/*/config/credentials.yaml)`).

**More APIs for an app you have:** ask *"add the issue link APIs to jira_cloud"*. To see first
what the service's published description has and `api.yaml` lacks:
```python
In [17]: app = curldex.load_app("jira_cloud")
In [18]: app.discover()                        # the missing APIs: name, method, path, description
In [19]: app.discover(add=["get-issue-link"])  # append one, by a name from that list (or a glob: "*-issue-link")
```
`discover(add=...)` writes `api.yaml` and `API.md` and reloads the app; the new APIs have no
example yet, which Claude (or you) then adds. The APIs an app leaves out on purpose are in its
`source: exclude:`, so `discover()` doesn't offer them again: for `google_calendar` it prints
`0 of 31 APIs in the description aren't in api.yaml`.

**Keep a session login for a website:** a cookie copied from the browser works as `auth: {type:
curl}` with a `Cookie` header; an automated login form needs a small Auth subclass (see the skill's
reference.md).

### By hand

The skill's [reference.md](.claude/skills/curldex-new-app/reference.md) is the complete format
reference, and [SKILL.md](.claude/skills/curldex-new-app/SKILL.md) the workflow, if you'd rather
write an app yourself: a folder `curldex/apps/<app>/config/` with `api.yaml` and
`credentials.example.yaml`. With a `source:` in `api.yaml`, `discover(add=["*"])` fills it from the
published description, as in *More APIs* above; it never writes `base_url` or `auth`, but prints what
the description suggests, as YAML to check and paste. Then, in IPython:
```python
In [1]: app = curldex.load_app("my_app")
In [2]: cmd = app.request("GET", "/some/path")   # try any path under base_url, with the credentials
In [3]: !{cmd} | jq .
In [4]: !curldex check my_app                # what's wrong with the app, found without calling the service
In [5]: app.reload(); app.write_docs()       # after editing api.yaml: read it again, rewrite API.md
In [6]: app.verify()                         # run the examples; record date, status and response shape
```

## The curldex command

The same features as a command: `!curldex ...` in IPython, and what Claude runs from its Bash tool.

```
curldex auth <app>              credentials for curl: curl -K "$(curldex auth <app>)" URL
curldex apps                    the apps, and whether each is ready
curldex <app> [api]             the app's APIs, or one API's help (`curldex <app> help` for all)
curldex help <app> [api]        the same: the one form to pre-approve for Claude, for every app
curldex docs <app>              write the app's API reference, curldex/apps/<app>/API.md
curldex login <app>             log in; --start prints the URL, --finish ADDRESS completes it
curldex discover <app>          the APIs api.yaml lacks from its source: description; --add NAME_OR_GLOB
curldex verify <app> [api]      run the examples (read APIs) and record that they work
curldex check <app>             what's wrong with an app package, found without calling the service
```

Exit codes: 0 ok, 1 HTTP or curl error, 2 usage error, 3 config or login problem.

Claude reads `CLAUDE.md`, asks `curldex help <app> <api>` for each API it needs, and runs the same
curl commands after you log in. `.claude/settings.json` pre-approves `curldex help`, `apps` and
`check` for every app, so a new app needs no settings change. Those three only read the config and
report; `curldex help` shows help and nothing else (`curldex help verify ...` is refused). They do
import an app's `__init__.py` (so loading runs that app's Python, as any import would); an app's code
is yours to trust, like any code in the repo.
`curldex login <app> --start` / `--finish` let Claude run the login while you only approve it in the
browser.

## The apps

`curldex apps` lists the ones in your copy.

| App | What | Setup |
|---|---|---|
| `google_calendar` | Calendars and events; `events_on()`, `add_event()` | [docs/google_setup.md](docs/google_setup.md) |
| `httpbin` | httpbin.org, which echoes requests: a reference for every request shape; YAML only | none |

Each app has its own `config/credentials.yaml` (from `credentials.example.yaml`; git-ignored) and its
own login.

## The config file

The full format is in `.claude/skills/curldex-new-app/reference.md`. In short:

```yaml
description: Google Calendar - calendars and events
base_url: https://www.googleapis.com/calendar/v3    # may hold ${key}: https://${subdomain}.example.com
auth: {type: oauth2, scopes: [https://www.googleapis.com/auth/calendar]}
headers: {}                                  # optional: sent with every call
curl_options: []                             # optional: more curl options, e.g. [--compressed]
source:                                      # optional: where the API is described, for discover
  url: https://www.googleapis.com/discovery/v1/apis/calendar/v3/rest     # or a file next to api.yaml
  exclude: [calendars-clear]                 # and include: globs of API names to keep
commands:
  list-events:
    description: List events in a time range
    method: GET                              # default GET
    path: /calendars/{calendar_id}/events    # {name}: a path param
    access: read                             # default from the method: GET read; POST, PATCH write; PUT, DELETE destructive
    params:
      calendar_id: {default: "${calendar_id}"}     # ${key}: a value from credentials.yaml
      timeMin: {description: RFC 3339 lower bound}  # in: query (GET, DELETE) or body (POST, PUT, PATCH) by default
      maxResults: {type: integer, default: 25}
    example: {timeMin: "2026-10-12T00:00:00-07:00", maxResults: 5}   # for help, API.md and verify
```

- **Auth types:**
  - `none`;
  - `bearer` (`prefix: Bot` for another word than Bearer);
  - `basic`;
  - `api_key` (in a header or the query);
  - `curl`: any curl config lines with `${key}` placeholders, so two headers, digest, a client
    certificate, AWS SigV4 or a cookie all work;
  - `oauth2` (browser login);
  - `oauth2_client` (client credentials).
- **Body formats:** `format: json` (default), `form`, `multipart` (with `type: file` params for
  uploads) or `raw`.

## Layout

```
curldex/        the core, its modules: the common interface and mechanism; names no service
  config.py endpoint.py curl.py webapp.py    a call (config.py also holds the errors)
  auth.py oauth2.py                          credentials to curl
  docs.py verify.py check.py                 docs, verified examples and checks
  discover.py                                from a published description to api.yaml
  cli.py                                     the command
  shared/       code a family of apps shares: google.py (Google's login and Discovery importer)
  apps/         one folder per web service: config/, API.md, and __init__.py if it has code
.claude/skills/curldex-new-app/   how Claude adds a service: SKILL.md (workflow), reference.md (formats)
demo/           google_calendar.ipy: Google Calendar from IPython, with curl only
tests/          offline: real curl against a local fake service
docs/           the Google setup guide, and a walkthrough of adding an app with Claude Code
.github/        CI: lint, the offline tests and a build check on pushes to master/main and on pull requests
CLAUDE.md       how Claude uses curldex in this project, and works on it
CHANGELOG.md CONTRIBUTING.md SECURITY.md   what changed; how to contribute; reporting a security problem
CODE_OF_CONDUCT.md                         how everyone taking part treats each other
LICENSE NOTICE  Apache License 2.0, and the attribution to keep with copies
```

Apps can also live outside the project: list their folders in `$CURLDEX_APPS`.

## Safety

- Credentials live only in owner-only, git-ignored files (`credentials.yaml`, `.state.json`,
  `.curlrc`), never in a command, an error message or the docs. Only `credentials.yaml` may read an
  environment variable or a file (`${env:VAR}`, `${file:PATH}`): `api.yaml`, which may come from a
  published description, can't, and `curldex check` flags it if it tries. An app whose `api.yaml`
  would put a credential into a command doesn't load.
- Credentials never go to another host: `request()` only reaches paths under the app's `base_url`,
  curl doesn't follow redirects, and an app's config can give curl only the options listed in the
  skill's reference.md, none of which sends credentials elsewhere or writes files. Others are
  refused when the app loads.
- Destructive APIs are flagged everywhere; `run()` asks before running one (or takes `yes=True`), and
  `verify()` runs only read APIs unless it's given one API by name and `yes=True` (`--yes` in the
  shell).
- `API.md` and `verified.yaml` hold commands and a response's shape — field names and types, not
  values; the keys of an object- or array-valued map (ids, usernames) are shown as `<key>`. A value
  given to `verify()` is recorded as a stand-in, such as `<event_id>`. So they can be committed and
  shared. (A response that is a map keyed by data to plain scalars is the one case whose keys can't
  be told from field names, so check such a record before sharing it.)
- `curl -v` and `--trace` print the Authorization header, token included; use `-D -` and
  `-w '%{http_code}'` instead.

## Development

To work on curldex (setup, tests, where code goes), see [CONTRIBUTING.md](CONTRIBUTING.md); to report
a security problem, see [SECURITY.md](SECURITY.md).

## License

curldex is licensed under the [Apache License, Version 2.0](LICENSE). Copyright 2026
Max Tan ([github.com/ytan88](https://github.com/ytan88)); see [NOTICE](NOTICE).
