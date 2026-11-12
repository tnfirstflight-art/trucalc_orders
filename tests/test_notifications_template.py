from odoo import tools
from odoo.tests import tagged

from ._notification_test_base import NotificationTestCase


@tagged("post_install", "-at_install", "trucalc_notifications")
class TestNotificationTemplate(NotificationTestCase):
    def test_exact_neutral_template_and_single_explicit_recipient(self):
        self._configure_rendering()
        order, _valuation, delivery = self._complete_and_delivery()
        order.sudo().message_subscribe(partner_ids=[self.bank_admin.partner_id.id])
        claimed = self.env[
            "trucalc.notification.delivery"
        ]._claim_next_due()
        mail = claimed._prepare_attempt()
        self.assertEqual(
            mail.subject,
            "TruCalc: Order %s is Complete" % order.order_number,
        )
        self.assertEqual(mail.email_to, self.requestor.email)
        self.assertFalse(mail.email_cc)
        self.assertFalse(mail.recipient_ids)
        self.assertNotIn(self.bank_admin.email, mail.email_to)
        self.assertFalse(mail.attachment_ids)
        self.assertFalse(mail.auto_delete)
        self.assertEqual(mail.state, "cancel")
        body = tools.html2plaintext(mail.body_html or "")
        self.assertIn("Your evaluation order", body)
        self.assertIn(order.order_number, body)
        self.assertIn("has been completed", body)
        self.assertIn("https://trucalc.example.test/my/trucalc/bank/orders/", mail.body_html)
        self.assertIn("#022f5b", mail.body_html)
        for prohibited in (
            order.borrower, order.property_address, order.loan_number,
            "Synthetic notes", "Approved.pdf", "Invoice.pdf",
        ):
            self.assertNotIn(prohibited, mail.body_html)

    def test_bad_host_fails_closed_before_attempt(self):
        self._configure_rendering()
        self.params.set_param("web.base.url", "https://attacker.example.test")
        _order, _valuation, delivery = self._complete_and_delivery()
        delivery = self.env[
            "trucalc.notification.delivery"
        ]._claim_next_due()
        self.assertFalse(delivery._prepare_attempt())
        self.assertEqual(delivery.state, "permanent_failure")
        self.assertEqual(delivery.failure_category, "portal_configuration")
        self.assertEqual(delivery.attempt_count, 0)
