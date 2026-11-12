from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from ._notification_test_base import NotificationTestCase
from ..models.notification_delivery import NotificationCaptureError


@tagged("post_install", "-at_install", "trucalc_notifications")
class TestNotificationCompletion(NotificationTestCase):
    def test_defaults_off_and_valid_completion_captures_without_transport(self):
        cron = self.env.ref(
            "trucalc_orders.ir_cron_phase_a_notification_dispatch"
        )
        self.assertFalse(cron.active)
        self._enable_capture()
        order, _valuation = self._ready_order()
        Mail = type(self.env["mail.mail"])
        with patch.object(Mail, "send", side_effect=AssertionError("transport called")):
            order.with_user(self.admin).action_complete_order()
        event = order.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "order_completed"
        )
        delivery = self.env["trucalc.notification.delivery"].sudo().search([
            ("event_id", "=", event.id),
        ])
        self.assertEqual((len(event), len(delivery)), (1, 1))
        self.assertEqual(delivery.state, "pending")
        self.assertFalse(delivery.mail_id)
        self.assertEqual(delivery.audit_ids.audit_type, "enqueued")

    def test_capture_failure_isolated_then_reconciled(self):
        self._enable_capture()
        order, _valuation = self._ready_order()
        Delivery = type(self.env["trucalc.notification.delivery"])
        with patch.object(
            Delivery, "_get_or_create_completion_delivery",
            side_effect=NotificationCaptureError("synthetic capture failure"),
        ):
            order.with_user(self.admin).action_complete_order()
        self.assertEqual(order.status, "completed")
        self.assertFalse(self.env["trucalc.notification.delivery"].sudo().search([
            ("order_id", "=", order.id),
        ]))
        counters = self.env[
            "trucalc.notification.delivery"
        ]._reconcile_missing_completion_intents()
        self.assertEqual(counters["created"], 1)

    def test_pre_activation_completion_never_backfills(self):
        self._enable_capture(fields.Datetime.now() + timedelta(hours=1))
        order, _valuation = self._ready_order()
        order.with_user(self.admin).action_complete_order()
        counters = self.env[
            "trucalc.notification.delivery"
        ]._reconcile_missing_completion_intents()
        self.assertEqual(counters["eligible_events"], 0)
        self.assertFalse(self.env["trucalc.notification.delivery"].sudo().search([
            ("order_id", "=", order.id),
        ]))
