import os
import sys
from dotenv import load_dotenv

load_dotenv()


class Config:
    AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    MANAGER_EMAIL: str = os.getenv("MANAGER_EMAIL", "")
    MONITORED_EMAIL: str = os.getenv("MONITORED_EMAIL", "")

    POLL_INTERVAL: int = int(os.getenv("POLL_INTERVAL", "60"))
    CALENDAR_ID: str = os.getenv("CALENDAR_ID", "primary")

    CREDENTIALS_PATH: str = os.getenv("CREDENTIALS_PATH", "/credentials/credentials.json")
    TOKEN_PATH: str = os.getenv("TOKEN_PATH", "/credentials/token.json")
    DB_PATH: str = os.getenv("DB_PATH", "/data/agent.db")

    MEETING_LOOKAHEAD_DAYS: int = int(os.getenv("MEETING_LOOKAHEAD_DAYS", "5"))
    MEETING_SLOT_COUNT: int = int(os.getenv("MEETING_SLOT_COUNT", "3"))
    WORKING_HOURS_START: int = int(os.getenv("WORKING_HOURS_START", "9"))
    WORKING_HOURS_END: int = int(os.getenv("WORKING_HOURS_END", "17"))
    DEFAULT_MEETING_DURATION: int = int(os.getenv("DEFAULT_MEETING_DURATION", "60"))
    SLOT_STEP_MINUTES: int = int(os.getenv("SLOT_STEP_MINUTES", "30"))

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "text")   # "text" | "json"

    # Set to "false" to label SALES_OUTREACH without sending a decline reply.
    AUTO_DECLINE_SALES: bool = os.getenv("AUTO_DECLINE_SALES", "true").lower() == "true"

    @classmethod
    def validate(cls) -> None:
        """Fail fast with a clear, actionable message if required config is missing."""
        errors: list[str] = []

        for name, value in {
            "AZURE_OPENAI_API_KEY": cls.AZURE_OPENAI_API_KEY,
            "AZURE_OPENAI_ENDPOINT": cls.AZURE_OPENAI_ENDPOINT,
            "MANAGER_EMAIL": cls.MANAGER_EMAIL,
        }.items():
            if not value:
                errors.append(f"  {name} is not set")

        for name, path in {
            "CREDENTIALS_PATH": cls.CREDENTIALS_PATH,
            "TOKEN_PATH": cls.TOKEN_PATH,
        }.items():
            if not os.path.exists(path):
                errors.append(f"  {name}={path!r} does not exist — run auth.py first")

        if errors:
            sys.exit("Configuration errors:\n" + "\n".join(errors))
