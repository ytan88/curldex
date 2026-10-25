# Security policy

curldex handles credentials for web services, so security reports matter and are welcome.

## Reporting a vulnerability

Please report it privately, not in a public issue: on GitHub, open the repository's **Security**
tab and choose **Report a vulnerability**. Include:

- the curldex version or commit;
- what happens, and the steps to reproduce it;
- what an attacker could gain.

Never include real secrets: replace tokens, client secrets, passwords and the contents of
`credentials.yaml`, `.state.json` or `.curlrc` with `XXX`.

Reports are answered as soon as possible. Once a fix is released, the report can be made public,
with credit to you if you'd like.

## What counts

curldex promises that:

- a secret never appears in a curl command, an error message, generated docs (`API.md`) or
  `verified.yaml`, and neither does a value given to `verify()`, which `verified.yaml` records as a
  stand-in; `verified.yaml` and `API.md` record a response's shape — field names and types, not
  values — and the keys of an object- or array-valued map are shown as `<key>` (a map keyed by data
  to plain scalars is the one shape whose keys can't be distinguished from field names);
- credentials go only to the app's own `base_url`: `request()` can't reach another host, and an
  app's config can give curl only the options that send credentials (its auth's lines, whatever
  the auth type) or tune the connection (`curl_options`), as the skill's `reference.md` lists them;
- `api.yaml`, which may come from a published API description, can't read the environment or local
  files, nor make curl upload a local file that the caller didn't name;
- the files that hold secrets are written owner-only (mode 600).

Anything that breaks one of these is a vulnerability. So is a way for an API description, a
response or an app's config to make curldex run a command or write outside the app's folder.

These promises are about an app's config files (`api.yaml`, `credentials.yaml`). An app's
`__init__.py` is Python code, and runs with your permissions like any other: review an app's code
before you use it, as you would any code.

## Supported versions

Fixes go into the latest release and the default branch.
