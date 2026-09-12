import json
import logging
import signal
import sys
import threading

from openai import AzureOpenAI

from agent.workflows.approval_flow import ApprovalFlow
from agent.google.calendar_client import CalendarClient
from agent.ai.classifier import Classifier, VALID_LABELS
from agent.config import Config
from agent.storage.database import init_db, is_processed, log_event, mark_processed
from agent.google.gmail_client import GmailClient
from agent.google.auth import get_credentials
from agent.workflows.orchestrator import EmailOrchestrator

logger = logging.getLogger("agent.main")

# URGENT is a Gmail modifier label, not a classifier output — added separately.
_GMAIL_LABELS_TO_CREATE = list(VALID_LABELS - {"SPAM"}) + ["URGENT", "Finance"]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """Single-line JSON records for log aggregation pipelines (Datadog, Loki, etc.)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    if Config.LOG_FORMAT.lower() == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
    logging.root.setLevel(getattr(logging, Config.LOG_LEVEL, logging.INFO))
    logging.root.handlers = [handler]


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def _build_orchestrator() -> EmailOrchestrator:
    """Construct all API clients and wire them into an orchestrator."""
    creds = get_credentials()
    gmail = GmailClient(creds)
    calendar = CalendarClient(creds)
    az_client = AzureOpenAI(
        api_key=Config.AZURE_OPENAI_API_KEY,
        azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
        api_version=Config.AZURE_OPENAI_API_VERSION,
        max_retries=4,
    )
    return EmailOrchestrator(
        gmail=gmail,
        calendar=calendar,
        classifier=Classifier(az_client),
        approval=ApprovalFlow(gmail, calendar),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    _setup_logging()
    Config.validate()
    logger.info("Starting AI Email & Calendar Assistant")

    init_db()
    orchestrator = _build_orchestrator()

    for label in _GMAIL_LABELS_TO_CREATE:
        orchestrator.gmail.ensure_label_exists(label)

    stop_event = threading.Event()

    def _on_shutdown(signum, frame):
        logger.info("Shutdown signal received — stopping after this cycle")
        stop_event.set()

    signal.signal(signal.SIGTERM, _on_shutdown)
    signal.signal(signal.SIGINT, _on_shutdown)

    logger.info(f"Agent ready — polling every {Config.POLL_INTERVAL}s")

    while not stop_event.is_set():
        try:
            message_ids = orchestrator.gmail.get_unread_inbox_messages()
            logger.debug(f"Poll: {len(message_ids)} unread message(s)")

            for msg_id in message_ids:
                if stop_event.is_set():
                    break
                if is_processed(msg_id):
                    continue
                try:
                    email_data = orchestrator.gmail.get_message(msg_id)
                    orchestrator.process(email_data)
                except Exception as exc:
                    logger.error(f"[{msg_id}] Processing error: {exc}", exc_info=True)
                    log_event(msg_id, "PROCESSING_ERROR", {"error": str(exc)})
                    mark_processed(msg_id, "ERROR", False, str(exc)[:200], "error")

        except BrokenPipeError:
            logger.warning("Broken pipe — reconnecting Google API clients")
            orchestrator = _build_orchestrator()

        except Exception as exc:
            logger.error(f"Poll cycle error: {exc}", exc_info=True)

        stop_event.wait(timeout=Config.POLL_INTERVAL)

    logger.info("Agent stopped cleanly")


if __name__ == "__main__":
    main()
