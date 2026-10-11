import logging
import re


_SENSITIVE_KEY = (
    r"(?:access[_-]?token|token|password2?|passwd|csrf[_-]?token|"
    r"client[_-]?secret|secret)"
)
_QUERY_OR_FORM_VALUE = re.compile(
    rf"(?i)(?P<prefix>(?:[?&;]|\b){_SENSITIVE_KEY}\s*=\s*)"
    r"(?P<value>[^&;\s\"'<>]+)"
)
_MAPPING_VALUE = re.compile(
    rf"(?i)(?P<prefix>['\"]?{_SENSITIVE_KEY}['\"]?\s*:\s*)"
    r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)"
)
_AUTHORIZATION_VALUE = re.compile(
    r"(?i)(?P<prefix>authorization\s*[:=]\s*(?:bearer|basic)\s+)"
    r"(?P<value>[^\s,;]+)"
)
_REDACTED = "<redacted>"


def redact_sensitive_log_data(value):
    """Redact URL, form, mapping, and Authorization credential values."""
    if not isinstance(value, str) or not value:
        return value
    value = _QUERY_OR_FORM_VALUE.sub(
        lambda match: match.group("prefix") + _REDACTED,
        value,
    )
    value = _MAPPING_VALUE.sub(
        lambda match: (
            match.group("prefix") + match.group("quote")
            + _REDACTED + match.group("quote")
        ),
        value,
    )
    return _AUTHORIZATION_VALUE.sub(
        lambda match: match.group("prefix") + _REDACTED,
        value,
    )


class SensitiveLogFilter(logging.Filter):
    """Prevent signed URLs and credentials from reaching configured handlers."""

    trucalc_sensitive_log_filter = True

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        record.msg = redact_sensitive_log_data(message)
        record.args = ()
        if record.exc_info:
            formatter = logging.Formatter()
            record.exc_text = redact_sensitive_log_data(
                formatter.formatException(record.exc_info)
            )
        if record.stack_info:
            record.stack_info = redact_sensitive_log_data(record.stack_info)
        return True


def _has_sensitive_filter(filters):
    return any(
        getattr(item, "trucalc_sensitive_log_filter", False)
        for item in filters
    )


def install_sensitive_log_filter():
    """Protect Odoo's configured output handlers and Werkzeug access logger."""
    werkzeug_logger = logging.getLogger("werkzeug")
    if not _has_sensitive_filter(werkzeug_logger.filters):
        werkzeug_logger.addFilter(SensitiveLogFilter())
    for handler in logging.getLogger().handlers:
        if not _has_sensitive_filter(handler.filters):
            handler.addFilter(SensitiveLogFilter())


install_sensitive_log_filter()
