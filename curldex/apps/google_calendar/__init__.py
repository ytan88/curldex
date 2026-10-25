# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""Google Calendar: its REST API as curl commands, plus calendar helpers.

    import curldex
    cal = curldex.load_app("google_calendar")          # a GoogleCalendar
    cal.login()                                      # once
    cal.apis()                                       # the APIs
    cal.help("list-events")                          # params and an example curl command
    cmd = cal.curl("list-events", maxResults=5)      # in IPython: !{cmd}
    cal.events_on()                                  # helper: today's events, in the calendar's time zone
    cal.add_event("Dentist", datetime(2026, 10, 12, 15, 0).astimezone(), minutes=45)

config/api.yaml is curated by hand: the API names, defaults and examples these helpers rely on.
`curldex discover google_calendar` lists the API's other methods and adds the ones you pick.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from curldex.shared.google import GoogleApp


class GoogleCalendar(GoogleApp):
    DIRECTORY = Path(__file__).parent

    def timezone(self) -> str:
        """The user's time zone, e.g. "America/Los_Angeles"."""
        return self.run("get-setting", setting="timezone")["value"]

    def events_between(self, start: datetime, end: datetime, calendar_id: str | None = None) -> list[dict]:
        """Events from `start` to `end` (recurring ones expanded), in start order; up to 2500."""
        values = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": True,
                  "orderBy": "startTime", "maxResults": 2500}
        if calendar_id:
            values["calendar_id"] = calendar_id
        return self.run("list-events", **values).get("items", [])

    def events_on(self, day: date | None = None, calendar_id: str | None = None) -> list[dict]:
        """Events on one day (default: today), in the user's time zone."""
        zone = ZoneInfo(self.timezone())
        start = datetime.combine(day or datetime.now(zone).date(), time(), zone)
        return self.events_between(start, start + timedelta(days=1), calendar_id)

    def add_event(self, summary: str, start: datetime, minutes: int = 30, **fields) -> dict:
        """Create an event; `start` must have a time zone. Other fields (description, location,
        attendees...) are passed on."""
        if start.tzinfo is None:
            raise ValueError("start needs a time zone, e.g. datetime(...).astimezone()")
        end = start + timedelta(minutes=minutes)
        return self.run("create-event", summary=summary, start={"dateTime": start.isoformat()},
                        end={"dateTime": end.isoformat()}, **fields)


App = GoogleCalendar
