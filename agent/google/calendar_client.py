import logging
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from agent.config import Config

logger = logging.getLogger(__name__)


def _parse_google_dt(value: str) -> datetime:
    """Parse a Google Calendar dateTime or all-day date string to UTC datetime."""
    if not value:
        return datetime.now(timezone.utc)
    if "T" in value:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    d = datetime.strptime(value, "%Y-%m-%d")
    return d.replace(tzinfo=timezone.utc)


def _format_slot(slot: dict) -> str:
    start: datetime = slot["start"]
    end: datetime = slot["end"]
    return (
        f"{start.strftime('%A, %B %d, %Y')} "
        f"{start.strftime('%I:%M %p')} – {end.strftime('%I:%M %p')} UTC"
    )


class CalendarClient:
    def __init__(self, credentials: Credentials) -> None:
        self._service = build("calendar", "v3", credentials=credentials)
        self._calendar_id = Config.CALENDAR_ID
        self._working_start = Config.WORKING_HOURS_START
        self._working_end = Config.WORKING_HOURS_END
        self._slot_step = timedelta(minutes=Config.SLOT_STEP_MINUTES)

    def get_events(self, start: datetime, end: datetime) -> list[dict]:
        result = (
            self._service.events()
            .list(
                calendarId=self._calendar_id,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute(num_retries=Config.API_RETRIES)
        )
        return result.get("items", [])

    def find_free_slots(
        self,
        duration_minutes: int,
        count: int,
        lookahead_days: int,
    ) -> list[dict]:
        """Return up to `count` free slots within `lookahead_days` business days."""
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        search_end = now + timedelta(days=lookahead_days)

        busy: list[tuple[datetime, datetime]] = []
        for ev in self.get_events(now, search_end):
            s = ev.get("start", {})
            e = ev.get("end", {})
            busy.append((
                _parse_google_dt(s.get("dateTime", s.get("date", ""))),
                _parse_google_dt(e.get("dateTime", e.get("date", ""))),
            ))

        slot_duration = timedelta(minutes=duration_minutes)
        minutes = now.minute
        check = (
            now.replace(minute=30, second=0)
            if minutes < 30
            else (now + timedelta(hours=1)).replace(minute=0, second=0)
        )

        free: list[dict] = []
        while len(free) < count and check < search_end:
            if check.weekday() >= 5:                          # skip weekends
                check = (check + timedelta(days=1)).replace(
                    hour=self._working_start, minute=0, second=0
                )
                continue
            if check.hour < self._working_start:
                check = check.replace(hour=self._working_start, minute=0, second=0)
                continue

            slot_end = check + slot_duration
            if slot_end.hour > self._working_end or (
                slot_end.hour == self._working_end and slot_end.minute > 0
            ):
                check = (check + timedelta(days=1)).replace(
                    hour=self._working_start, minute=0, second=0
                )
                continue

            conflict_end = None
            for ev_start, ev_end in busy:
                if check < ev_end and slot_end > ev_start:
                    if conflict_end is None or ev_end > conflict_end:
                        conflict_end = ev_end

            if conflict_end:
                check = conflict_end
            else:
                free.append({"start": check, "end": slot_end})
                check += self._slot_step

        return free

    def create_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        attendee_email: str,
        description: str = "",
    ) -> str:
        """Create a calendar event and return its event ID."""
        body = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
            "attendees": [{"email": attendee_email}],
            "reminders": {"useDefault": True},
        }
        event = (
            self._service.events()
            .insert(calendarId=self._calendar_id, body=body, sendUpdates="all")
            .execute(num_retries=Config.API_RETRIES)
        )
        logger.info(f"Created calendar event: {event.get('htmlLink')}")
        return event["id"]


def format_slots(slots: list[dict]) -> str:
    return "\n".join(f"  [{i}] {_format_slot(s)}" for i, s in enumerate(slots, 1))
