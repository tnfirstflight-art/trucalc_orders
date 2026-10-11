import logging

from odoo.tests import TransactionCase, tagged

from ..utils.logging import SensitiveLogFilter, redact_sensitive_log_data


@tagged("post_install", "-at_install", "trucalc_logging_redaction")
class TestLoggingRedaction(TransactionCase):
    def test_sensitive_query_and_request_body_values_are_redacted(self):
        synthetic = "synthetic-secret-value-for-redaction-test"
        message = (
            'GET /web/signup?token=%s&next=/web HTTP/1.1 '
            "body={'password': '%s', 'password2': '%s'}"
        ) % (synthetic, synthetic, synthetic)
        redacted = redact_sensitive_log_data(message)
        redacted_twice = redact_sensitive_log_data(redacted)
        self.assertNotIn(synthetic, redacted)
        self.assertEqual(redacted_twice, redacted)
        self.assertIn("GET /web/signup?token=<redacted>&next=/web HTTP/1.1", redacted)
        self.assertIn("'password': '<redacted>'", redacted)

    def test_exception_and_authorization_values_are_redacted(self):
        synthetic = "synthetic-error-secret-for-redaction-test"
        try:
            raise ValueError("failed URL /web/signup?token=%s" % synthetic)
        except ValueError:
            record = logging.LogRecord(
                "trucalc.test", logging.ERROR, __file__, 1,
                "Authorization: Bearer %s", (synthetic,), exc_info=True,
            )
            record.exc_info = __import__("sys").exc_info()
        self.assertTrue(SensitiveLogFilter().filter(record))
        self.assertNotIn(synthetic, record.getMessage())
        self.assertNotIn(synthetic, record.exc_text)
        self.assertIn("Authorization: Bearer <redacted>", record.getMessage())

    def test_filter_is_installed_on_access_logger_and_output_handlers(self):
        self.assertTrue(any(
            getattr(item, "trucalc_sensitive_log_filter", False)
            for item in logging.getLogger("werkzeug").filters
        ))
        for handler in logging.getLogger().handlers:
            self.assertTrue(any(
                getattr(item, "trucalc_sensitive_log_filter", False)
                for item in handler.filters
            ))
