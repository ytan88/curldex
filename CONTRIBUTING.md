# Contributing to curldex

Thank you for helping. Bug reports, new apps and fixes are all welcome. Everyone taking part follows
the [code of conduct](CODE_OF_CONDUCT.md).

## Set up

You need Python 3.10 or newer, [uv](https://docs.astral.sh/uv/) and curl 7.87 or newer.

```bash
git clone https://github.com/ytan88/curldex.git
cd curldex
uv sync                 # .venv with curldex (editable) and the dev tools
uv run pytest -q        # offline: real curl against a local fake service, a few seconds
uv run ruff check
uv build                # wheel and sdist in dist/
```

## Where code goes

- **The core** is the modules directly in `curldex/`: the common interface and mechanism. It names
  no service.
- **An app** is `curldex/apps/<app>/`: `config/api.yaml`, `config/credentials.example.yaml`, and an
  `__init__.py` only if the app needs code (helpers, an unusual login, another description format).
- **Code a family of apps shares**, such as one vendor's login, goes in `curldex/shared/<family>.py`.
- Extend by subclassing `WebApp`, `Auth` or `Importer`, not with registries or decorators.

To add a service, follow the skill in `.claude/skills/curldex-new-app/` (with Claude Code, or by
hand); its `reference.md` has every file format.

## Pull requests

- Run `uv run pytest -q` and `uv run ruff check`; both must pass, and CI runs them on Python 3.10
  and 3.15. A change in behaviour comes with a test. Tests stay offline: they use the fake service
  in `tests/conftest.py`, never a real one.
- For a change to an app, run `.venv/bin/curldex check <app>` until it reports no errors. Verify
  the APIs you added or changed against the real service (`.venv/bin/curldex verify <app> <api>`;
  writes only on test data you made), or say in the pull request why you couldn't. Then regenerate
  its `API.md` with `.venv/bin/curldex docs <app>`. `API.md` and `config/verified.yaml` are
  generated: don't edit them by hand.
- Never commit credentials, tokens or real account data. `credentials.yaml`, `.state.json` and
  `.curlrc` are git-ignored; examples in `api.yaml` use made-up values, such as `sam@example.com`.
  Builds pack the package folder, where each user's secret files live, so `source-exclude` in
  `pyproject.toml` keeps them out: a new secret file goes there, in `.gitignore`, in the deny rules
  of `.claude/settings.json`, and in the files the CI secret check plants (`.github/workflows/ci.yml`).
- The curl options an app's config may use are a security boundary (`SENDS` and `reads_file()` in
  `curldex/auth.py`, `CURL_OPTIONS` in `curldex/webapp.py`): a pull request that adds one says why it
  can't send the credentials to another host, send a local file or write files.
- Add a line for the change to `CHANGELOG.md`, under "Unreleased".
- New Python files start with the license header:
  ```python
  # Copyright 2026 Max Tan
  # SPDX-License-Identifier: Apache-2.0
  ```
- Keep a pull request to one change, and say in its description what it changes and why.

## Reporting a security problem

Please don't open a public issue: see [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE), as section 5 of the license describes.
