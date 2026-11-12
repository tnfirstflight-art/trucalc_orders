from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from ._notification_test_base import NotificationTestCase
from ..models.notification_delivery import NotificationResolutionError


@tagged("post_install", "-at_install", "trucalc_notifications")
class TestNotificationSecurity(NotificationTestCase):
    def test_delivery_is_internal_and_company_scoped(self):
        _order, _valuation, delivery = self._complete_and_delivery()
        with self.assertRaises(AccessError):
            delivery.with_user(self.requestor).check_access("read")
        self.assertTrue(delivery.with_user(self.ops).has_access("read"))
        self.assertFalse(delivery.with_user(self.foreign_admin).has_access("read"))
        with self.assertRaises(AccessError):
            delivery.with_user(self.ops).write({"state": "sent"})

    def test_original_role_and_normal_user_access_rechecked(self):
        order, _valuation, delivery = self._complete_and_delivery()
        self.assertEqual(
            delivery._resolve_bank_order_completed_v1()["recipient"],
            self.requestor,
        )
        User = type(self.requestor)
        original_role = User._trucalc_bank_role_key

        def view_only(user):
            if user.id == self.requestor.id:
                return "view_only"
            return original_role(user)

        with patch.object(User, "_trucalc_bank_role_key", view_only):
            with self.assertRaises(NotificationResolutionError) as caught:
                delivery._resolve_bank_order_completed_v1()
        self.assertEqual(caught.exception.category, "recipient_role_changed")
        with patch.object(
            type(order), "check_access", side_effect=AccessError("synthetic denial"),
        ):
            with self.assertRaises(NotificationResolutionError) as caught:
                delivery._resolve_bank_order_completed_v1()
        self.assertEqual(caught.exception.category, "recipient_no_access")

    def test_original_bank_administrator_is_eligible(self):
        self.requestor.with_user(self.admin)._trucalc_change_bank_role(
            self.company, "administrator",
        )
        _order, _valuation, delivery = self._complete_and_delivery()
        resolved = delivery._resolve_bank_order_completed_v1()
        self.assertEqual(resolved["recipient"], self.requestor)
        self.assertNotEqual(resolved["recipient"], self.bank_admin)

    def test_inactive_moved_bank_and_invalid_identity_fail_closed(self):
        _order, _valuation, delivery = self._complete_and_delivery()
        self.requestor.sudo().write({"active": False})
        with self.assertRaises(NotificationResolutionError) as caught:
            delivery._resolve_bank_order_completed_v1()
        self.assertEqual(caught.exception.category, "recipient_inactive")
        self.requestor.sudo().with_context(active_test=False).write({"active": True})

        User = type(self.requestor)
        with patch.object(
            User, "_trucalc_bank_identity", return_value=self.other_company,
        ):
            with self.assertRaises(NotificationResolutionError) as caught:
                delivery._resolve_bank_order_completed_v1()
        self.assertEqual(caught.exception.category, "tenant_mismatch")

        with patch.object(
            User, "_trucalc_bank_identity",
            side_effect=AccessError("synthetic invalid persona"),
        ):
            with self.assertRaises(NotificationResolutionError) as caught:
                delivery._resolve_bank_order_completed_v1()
        self.assertEqual(caught.exception.category, "recipient_bank_changed")

        with patch.object(
            User, "_trucalc_normalize_login",
            side_effect=ValidationError("synthetic invalid address"),
        ):
            with self.assertRaises(NotificationResolutionError) as caught:
                delivery._resolve_bank_order_completed_v1()
        self.assertEqual(caught.exception.category, "recipient_email_missing")

    def test_address_change_before_artifact_uses_current_managed_address(self):
        self._configure_rendering()
        _order, _valuation, delivery = self._complete_and_delivery()
        new_address = "phase-a-requestor-updated@example.test"
        self.requestor.sudo().write({
            "login": new_address,
            "email": new_address,
        })
        delivery = self.env[
            "trucalc.notification.delivery"
        ]._claim_next_due()
        mail = delivery._prepare_attempt()
        self.assertEqual(mail.email_to, new_address)

    def test_address_change_after_artifact_never_rewrites_recipient(self):
        self._configure_rendering()
        _order, _valuation, delivery = self._complete_and_delivery()
        delivery = self.env[
            "trucalc.notification.delivery"
        ]._claim_next_due()
        mail = delivery._prepare_attempt()
        original_address = mail.email_to
        delivery._finalize_transport_result(
            "retryable", "smtp_connect_transient", "connect",
        )
        new_address = "phase-a-requestor-later@example.test"
        self.requestor.sudo().write({
            "login": new_address,
            "email": new_address,
        })
        delivery._controlled_write({"next_attempt_at": fields.Datetime.now()})
        delivery = self.env[
            "trucalc.notification.delivery"
        ]._claim_next_due()
        self.assertFalse(delivery._prepare_attempt())
        self.assertEqual(delivery.state, "permanent_failure")
        self.assertEqual(
            delivery.failure_category, "recipient_email_changed_after_artifact",
        )
        self.assertEqual(mail.email_to, original_address)
        self.assertNotEqual(mail.email_to, new_address)

    def test_external_public_and_reviewer_only_have_no_model_access(self):
        _order, _valuation, delivery = self._complete_and_delivery()
        reviewer_only = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Phase A Reviewer Only",
            "login": "phase-a-reviewer-only@example.test",
            "email": "phase-a-reviewer-only@example.test",
            "company_id": self.company.id,
            "company_ids": [Command.set(self.company.ids)],
            "group_ids": [Command.set([self.env.ref("base.group_user").id])],
        })
        self.env.cr.execute(
            "INSERT INTO res_groups_users_rel (uid, gid) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (
                reviewer_only.id,
                self.env.ref("trucalc_orders.group_trucalc_reviewer").id,
            ),
        )
        self.env.invalidate_all()
        for actor in (
            self.requestor, self.bank_admin, self.vendor_user,
            reviewer_only, self.env.ref("base.public_user"),
        ):
            self.assertFalse(delivery.with_user(actor).has_access("read"))
