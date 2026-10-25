# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""The apps: one folder per web service, each with config/api.yaml. An app that needs code is also a
package whose class subclasses curldex.WebApp (set as `App`). Code that several apps share is in
curldex/shared/. `curldex apps` lists them; to add one, Claude follows
.claude/skills/curldex-new-app/SKILL.md.
"""
