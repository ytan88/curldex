# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""curldex: find, document and run the right curl command for a web API.

  curldex auth <app>              credentials for curl: curl -K "$(curldex auth <app>)" URL
  curldex apps                    the apps, and whether each is ready
  curldex <app> [api]             the app's APIs, or one API's help (`curldex <app> help` for all)
  curldex help <app> [api]        the same: the one form to pre-approve for Claude, for every app
  curldex docs <app>              write the app's API reference, API.md
  curldex login <app>             log in; --start prints the URL, --finish ADDRESS completes it
  curldex discover <app>          the APIs api.yaml lacks from its source: description; --add NAME_OR_GLOB
  curldex verify <app> [api]      run the examples (read APIs) and record that they work
  curldex check <app>             what's wrong with an app package, found without calling the service

Exit codes: 0 ok, 1 HTTP or curl error, 2 usage error, 3 config or login problem.
Apps live in curldex/apps/, and in the folders listed in $CURLDEX_APPS. `curldex --version` prints the version.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .check import check
from .config import CurldexError, UsageError, short
from .docs import table
from .webapp import COMMANDS, app_names, load_app


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        asked = argv[:1] == ["help"]
        if asked:  # read-only, so one permission rule covers every app: never another command
            if argv[1:2] and argv[1] in COMMANDS:
                raise UsageError(f"`curldex help` only shows an app's APIs (curldex help <app> [api]); "
                                 f"run `curldex {argv[1]} ...` without it")
            argv = argv[1:]
        if not argv or argv[0] in ("-h", "--help"):
            print(__doc__.strip())
            return 0 if argv or asked else 2  # bare `curldex` is a usage error; asking for help isn't
        if argv[0] in ("-V", "--version"):
            print(f"curldex {__version__}")
            return 0
        if argv[0] not in COMMANDS:
            return show(*argv)
        if argv[0] == "verify" and "--yes" in argv:  # last, so Python < 3.13 parses `verify app --yes api`
            argv = [*(a for a in argv if a != "--yes"), "--yes"]
        args = parser().parse_args(argv)
        return args.run(args) or 0
    except CurldexError as e:
        print(f"curldex: {e}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:  # `curldex help <app> | head`: the reader has gone
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1


def show(name: str, *rest: str) -> int:
    """`curldex <app>`: the APIs; `curldex <app> <api>`: one API's help; `curldex <app> help`: all."""
    app = load_app(name)
    if rest[:1] == ("help",):
        rest = rest[1:]
        if not rest:
            app.help()
            return 0
    if len(rest) > 1:
        raise UsageError(f"usage: curldex {name} [api]")
    if rest:
        app.help(rest[0])
    else:
        app.apis()
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="curldex", description="See `curldex --help`.")
    commands = top.add_subparsers(dest="command", required=True)

    def command(name, run, about):
        sub = commands.add_parser(name, help=about, description=about)
        sub.set_defaults(run=run)
        if name != "apps":
            sub.add_argument("app")
        return sub

    command("apps", run_apps, "the apps, and whether each is ready")
    command("auth", lambda a: print(load_app(a.app).auth_file()), "write curl's credentials file; print its path")
    command("docs", lambda a: print(load_app(a.app).write_docs()), "write the app's API.md")
    login = command("login", run_login, "log in to the app's account")
    steps = login.add_mutually_exclusive_group()
    steps.add_argument("--start", action="store_true", help="only print the URL to open")
    steps.add_argument("--finish", metavar="ADDRESS", help="finish with the address the browser landed on")
    login.add_argument("--no-browser", action="store_true", help="don't open a browser")
    discover = command("discover", run_discover, "list the APIs api.yaml lacks from the description its source: names")
    discover.add_argument("--add", nargs="+", default=(), metavar="NAME_OR_GLOB", help="append these APIs")
    verify = command("verify", run_verify, "run the examples and record that they work")
    verify.add_argument("api", nargs="?")
    verify.add_argument("values", nargs="*", metavar="PARAM=VALUE", help="values instead of the example's")
    verify.add_argument("--yes", action="store_true", help="also run write and destructive APIs")
    command("check", run_check, "what's wrong with an app package, found without calling the service")
    return top


def run_apps(args: argparse.Namespace) -> None:
    rows = [_app_row(name) for name in app_names()]
    print(table(["App", "Status", "Description"], rows) if rows else "no apps yet")


def _app_row(name: str) -> list[str]:
    try:
        app = load_app(name)
    except Exception as e:  # a broken app shouldn't hide the others
        return [name, f"error: {e}", ""]
    return [name, short(app.status(), 90), short(app.description, 60)]


def run_login(args: argparse.Namespace) -> None:
    app = load_app(args.app)
    if args.start:
        print(app.auth.login_start())
        print(f"Open that URL and allow access, then run: curldex login {app.name} --finish 'ADDRESS'\n"
              "with the whole address the browser lands on (the page may say it can't connect).", file=sys.stderr)
    elif args.finish is not None:  # even "": an empty address is an error, not a login that waits
        app.auth.login_finish(args.finish)
        print(f"{app.name}: logged in")
    else:
        app.login(open_browser=not args.no_browser)


def run_discover(args: argparse.Namespace) -> None:
    load_app(args.app).discover(args.add)


def run_check(args: argparse.Namespace) -> int:
    app = load_app(args.app)
    problems = check(app)
    print(f"{app.name}: {len(app.endpoints)} APIs, auth {app.auth.KIND}")
    for problem in problems:
        print(f"  {problem}")
    errors = sum(p.startswith("error") for p in problems)
    print(f"{errors} errors, {len(problems) - errors} warnings" if problems else "ok")
    return 3 if errors else 0


def run_verify(args: argparse.Namespace) -> int:
    app = load_app(args.app)
    params = app.endpoint(args.api).params if args.api else {}
    values = {}
    for pair in args.values:
        key, sep, text = pair.partition("=")
        if not sep:
            raise UsageError(f"expected PARAM=VALUE, got {pair!r}")
        values[key] = text
        if key in params and params[key].type not in ("string", "file"):  # numbers, true/false, objects, lists
            try:
                values[key] = json.loads(text)
            except ValueError:
                raise UsageError(f"{key} expects {params[key].type} as JSON, got {text!r}") from None
    results = app.verify(args.api, args.yes, **values)
    if args.api and not results:  # the API named was skipped; verify() said why
        return 2
    return 1 if any(not r.ok for r in results) else 0
