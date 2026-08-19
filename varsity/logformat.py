"""One-line JSON log records, so production logs can be searched.

Railway, CloudWatch and every log aggregator will happily store plain text, but
you can't ask plain text "show me every failed Pesepay callback last Tuesday".
A JSON object per line costs nothing and makes that a filter.

Development keeps the human-readable formatter — see LOGGING in settings.
"""

import json
import logging


# Everything LogRecord sets up on its own. Anything else on the record was put
# there by a caller passing extra={...}, and belongs in the output.
_STANDARD = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"asctime", "message", "taskName"}


class JSONFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # A caller's extra={} can hold a model instance or anything else that
        # doesn't serialise. Logging must never be the thing that raises.
        return json.dumps(payload, default=repr, ensure_ascii=False)
