# Changelog

What changed in each version of curldex. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the versions follow
[Semantic Versioning](https://semver.org/).

## Unreleased

## 0.1.0 - 2026-10-25

The first public version:

- **The core:** an app is `config/api.yaml` (its APIs, each with an example) and a credentials
  template. `curldex.load_app()`, `apis()`, `help()`, `curl()`, `run()`, `request()`, `docs()`,
  `verify()` and `discover()` in IPython, and the `curldex` command for the shell and for Claude.
- **Auth:** `none`, `bearer`, `basic`, `api_key`, `curl` (curl config lines), `oauth2` (a browser
  login with PKCE, refreshed by itself) and `oauth2_client`. The credentials reach curl from a file,
  never in a command.
- **Finding APIs:** importers for OpenAPI 3 and Google API Discovery documents; `discover` lists what
  `api.yaml` lacks and appends the APIs chosen.
- **Checks:** `curldex check` finds what's wrong with an app without calling the service. An app's
  config may give curl only the options listed in the skill's reference.md, so it can't send the
  credentials to another host, send a local file or write files.
- **Apps:** `google_calendar` and `httpbin`; the Claude Code skill `curldex-new-app` adds more; the
  IPython demo `demo/google_calendar.ipy`.
