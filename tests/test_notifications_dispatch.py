import smtplib
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from ._notification_test_base import NotificationTestCase


@tagged("post_install", "-at_install", "trucalc_notifications")
class TestNotificationDispatch(NotificationTestCase):
    def _prepared(self):
        self._configure_rendering()
        _order, _valuation, delivery = self._complete_and_delivery()
        claimed = self.env[
            "trucalc.notification.delivery"
        ]._claim_next_due()
        self.assertEqual(claimed, delivery)
        mail = claimed._prepare_attempt()
        self.assertEqual(mail.state, "cancel")
        return claimed, mail

    def test_held_mail_isolated_and_confirmed_acceptance(self):
        delivery, mail = self._prepared()
        self.assertFalse(self.env["mail.mail"].sudo().search([
            ("id", "=", mail.id), ("state", "in", ("outgoing", "exception")),
        ]))
        native_selected = []

        def observe_native_queue(records, **_kwargs):
            native_selected.extend(records.ids)
            return True

        with patch.object(type(mail), "send", observe_native_queue):
            self.env["mail.mail"].process_email_queue(email_ids=[mail.id])
        self.assertNotIn(mail.id, native_selected)

        def fake_accept(records, **_kwargs):
            records.write({"state": "sent"})
            return True

        with patch.object(type(mail), "send", fake_accept):
            delivery._execute_attempt()
        self.assertEqual(delivery.state, "sent")
        self.assertTrue(delivery.transport_accepted_at)

    def test_confirmed_transient_retry_and_ambiguous_unknown(self):
        delivery, mail = self._prepared()
        with patch.object(
            type(mail), "send", side_effect=ConnectionRefusedError(),
        ):
            delivery._execute_attempt()
        self.assertEqual(delivery.state, "retryable_failure")
        self.assertEqual(delivery.failure_category, "smtp_connect_transient")
        self.assertLessEqual(
            delivery.next_attempt_at,
            fields.Datetime.now() + timedelta(minutes=2),
        )

        delivery._controlled_write({"next_attempt_at": fields.Datetime.now()})
        delivery = self.env[
            "trucalc.notification.delivery"
        ]._claim_next_due()
        mail = delivery._prepare_attempt()
        with patch.object(
            type(mail), "send", side_effect=smtplib.SMTPServerDisconnected(),
        ):
            delivery._execute_attempt()
        self.assertEqual(delivery.state, "unknown")
        self.assertFalse(delivery.next_attempt_at)

    def test_expired_claim_before_attempt_reclaims_but_after_attempt_is_unknown(self):
        self._configure_rendering()
        _order, _valuation, delivery = self._complete_and_delivery()
        claimed = self.env[
            "trucalc.notification.delivery"
        ]._claim_next_due()
        claimed._controlled_write({
            "lease_expires_at": fields.Datetime.now() - timedelta(minutes=1),
        })
        result = self.env[
            "trucalc.notification.delivery"
        ]._recover_expired_claims()
        self.assertEqual(result["reclaimed"], 1)
        self.assertEqual(delivery.state, "pending")
        self.env.flush_all()
        claimed = self.env[
            "trucalc.notification.delivery"
        ]._claim_next_due()
        claimed._prepare_attempt()
        claimed._controlled_write({
            "lease_expires_at": fields.Datetime.now() - timedelta(minutes=1),
        })
        result = self.env[
            "trucalc.notification.delivery"
        ]._recover_expired_claims()
        self.assertEqual(result["unknown"], 1)
        self.assertEqual(delivery.state, "unknown")
