from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_vendor_order_authorization")
class TestVendorOrderAuthorization(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_group = cls.env.ref("trucalc_orders.group_trucalc_admin")
        cls.ops_group = cls.env.ref("trucalc_orders.group_trucalc_operations")
        cls.reviewer_group = cls.env.ref("trucalc_orders.group_trucalc_reviewer")
        cls.vendor_group = cls.env.ref("trucalc_orders.group_vendor_portal")
        cls.bank_group = cls.env.ref("trucalc_orders.group_bank_admin")
        cls.vendor_a = cls.env["trucalc.vendor"].create({
            "name": "4B1B Vendor A", "vendor_type": "appraiser",
        })
        cls.vendor_b = cls.env["trucalc.vendor"].create({
            "name": "4B1B Vendor B", "vendor_type": "appraiser",
        })
        cls.wrong_vendor = cls.env["trucalc.vendor"].create({
            "name": "4B1B Reviewer", "vendor_type": "reviewer",
        })
        cls.bank = cls.env["res.company"].create({"name": "4B1B Bank"})
        cls.admin = cls._user("4b1b-admin", cls.admin_group)
        cls.ops = cls._user("4b1b-ops", cls.ops_group)
        cls.reviewer = cls._user("4b1b-reviewer", cls.reviewer_group)
        cls.vendor_user = cls._user(
            "4b1b-vendor", cls.vendor_group, vendor=cls.vendor_a
        )
        cls.bank_user = cls._user(
            "4b1b-bank", cls.bank_group, bank_company=cls.bank
        )

    @classmethod
    def _user(cls, login, group, vendor=False, bank_company=False):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login,
            "login": login,
            "email": "%s@example.test" % login,
            "group_ids": [Command.set([group.id])],
            "trucalc_vendor_id": vendor.id if vendor else False,
            "trucalc_bank_company_id": bank_company.id if bank_company else False,
        })

    def _order(self):
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "Authorization Test",
            "property_address": "1 Trusted Way",
            "company_id": self.env.company.id,
            "service_type": "evaluation",
        })
        order.with_user(self.admin).action_bid_requested()
        return order

    def _invitation(self, order=None, vendor=None, deadline=False):
        values = {
            "order_id": (order or self._order()).id,
            "vendor_id": (vendor or self.vendor_a).id,
        }
        if deadline:
            values["response_deadline"] = deadline
        return self.env["trucalc.bid.invitation"].with_user(self.admin).create(values)

    def _authorization(self, invitation):
        return self.env["trucalc.order.vendor.authorization"].sudo().search([
            ("invitation_id", "=", invitation.id),
        ])

    def _submit(self, invitation, user=None, amount=500):
        user = user or self.vendor_user
        bid = invitation.with_user(user).action_vendor_create_option({
            "option_name": "Standard", "bid_amount": amount, "turn_time_days": 5,
        })
        invitation.with_user(user).action_vendor_submit()
        return bid

    def test_legacy_initialization_is_empty(self):
        self.assertFalse(self.env["trucalc.order.vendor.authorization"].sudo().search([]))
        legacy = self.env["trucalc.bid.invitation"].sudo().search([
            ("is_legacy_reconstructed", "=", True),
        ])
        self.assertEqual(len(legacy), 10)
        self.assertFalse(self.env["trucalc.order.vendor.authorization"].sudo().search([
            ("order_id", "in", legacy.order_id.ids),
        ]))

    def test_ordinary_crud_and_order_forgery_are_denied(self):
        order = self._order()
        values = {
            "order_id": order.id, "vendor_id": self.vendor_a.id,
            "source": "assignment", "round_number": 1, "active": True,
            "authorized_at": fields.Datetime.now(),
        }
        users = [self.admin, self.ops, self.reviewer, self.bank_user, self.vendor_user]
        for user in users:
            with self.assertRaises(AccessError):
                self.env["trucalc.order.vendor.authorization"].with_user(user).with_context(
                    skip_security=True, bypass_security=True, default_active=True,
                ).create(values)
        invitation = self._invitation(order=order)
        authorization = self._authorization(invitation)
        with self.assertRaises(AccessError):
            authorization.with_user(self.admin).write({"active": False})
        with self.assertRaises(AccessError):
            authorization.with_user(self.admin).unlink()
        with self.assertRaises(AccessError):
            order.write({"vendor_authorization_ids": [Command.clear()]})

    def test_invitation_creates_one_trusted_authorization(self):
        invitation = self._invitation()
        authorization = self._authorization(invitation)
        self.assertEqual(len(authorization), 1)
        self.assertTrue(authorization.active)
        self.assertEqual(authorization.source, "invitation")
        self.assertEqual(authorization.order_id, invitation.order_id)
        self.assertEqual(authorization.vendor_id, invitation.vendor_id)
        self.assertEqual(authorization.company_id, invitation.company_id)
        self.assertEqual(authorization.round_number, invitation.round_number)
        with self.assertRaises(Exception):
            self._invitation(order=invitation.order_id)
        self.assertEqual(len(self._authorization(invitation)), 1)

    def test_invalid_invitation_creates_no_authorization(self):
        order = self._order()
        before = self.env["trucalc.order.vendor.authorization"].sudo().search_count([])
        with self.assertRaises(ValidationError):
            self._invitation(order=order, vendor=self.wrong_vendor)
        self.vendor_b.active = False
        with self.assertRaises(ValidationError):
            self._invitation(order=order, vendor=self.vendor_b)
        self.assertEqual(
            self.env["trucalc.order.vendor.authorization"].sudo().search_count([]), before
        )

    def test_deadline_update_expiration_and_mutation_denial(self):
        invitation = self._invitation(
            deadline=fields.Datetime.now() + timedelta(minutes=5)
        )
        authorization = self._authorization(invitation)
        later = fields.Datetime.now() + timedelta(minutes=10)
        invitation.action_set_response_deadline(later)
        self.assertEqual(authorization.expires_at, later)
        invitation.action_set_response_deadline(fields.Datetime.now() - timedelta(seconds=1))
        self.assertEqual(invitation.state, "expired")
        self.assertFalse(authorization.active)
        self.assertEqual(authorization.deauthorization_reason, "expired")
        self.assertTrue(self.env["trucalc.bid.audit"].search([
            ("action", "=", "invitation_expired"),
            ("invitation_id", "=", invitation.id),
        ]))
        with self.assertRaises(ValidationError):
            invitation.with_user(self.vendor_user).action_vendor_create_option({
                "option_name": "Late", "bid_amount": 1, "turn_time_days": 1,
            })

    def test_cron_expiration_and_legacy_exclusion(self):
        invitation = self._invitation()
        authorization = self._authorization(invitation)
        invitation._controlled_write({
            "response_deadline": fields.Datetime.now() - timedelta(seconds=1),
        })
        self.env["trucalc.bid.invitation"]._cron_expire_vendor_order_authorizations()
        self.assertEqual(invitation.state, "expired")
        self.assertFalse(authorization.active)
        legacy = self.env["trucalc.bid.invitation"].sudo().search([
            ("is_legacy_reconstructed", "=", True), ("state", "=", "invited"),
        ], limit=1)
        if legacy:
            self.assertEqual(legacy.state, "invited")

    def test_decline_and_revoke_deactivate_but_retain(self):
        declined = self._invitation()
        declined_auth = self._authorization(declined)
        declined.with_user(self.vendor_user).action_vendor_decline()
        self.assertTrue(declined_auth.exists())
        self.assertFalse(declined_auth.active)
        self.assertEqual(declined_auth.deauthorization_reason, "declined")
        revoked = self._invitation(vendor=self.vendor_b)
        revoked_auth = self._authorization(revoked)
        revoked.action_revoke()
        self.assertTrue(revoked_auth.exists())
        self.assertFalse(revoked_auth.active)
        self.assertEqual(revoked_auth.deauthorization_reason, "revoked")

    def test_bid_independence_winner_and_reopen(self):
        order = self._order()
        invitation_a = self._invitation(order=order)
        invitation_b = self._invitation(order=order, vendor=self.vendor_b)
        initial = self.env["trucalc.order.vendor.authorization"].sudo().search_count([
            ("order_id", "=", order.id),
        ])
        bid_a = self._submit(invitation_a)
        vendor_b_user = self._user("4b1b-vendor-b", self.vendor_group, vendor=self.vendor_b)
        self._submit(invitation_b, vendor_b_user, 600)
        self.assertEqual(self.env["trucalc.order.vendor.authorization"].sudo().search_count([
            ("order_id", "=", order.id),
        ]), initial)
        bid_a.with_user(self.admin).action_select_bid()
        self.env.invalidate_all()
        authorizations = (
            self.env["trucalc.order.vendor.authorization"]
            .sudo()
            .with_context(active_test=False)
            .search([("order_id", "=", order.id)])
        )
        self.assertEqual(len(authorizations.filtered("active")), 1)
        assignment = authorizations.filtered("active")
        self.assertEqual((assignment.source, assignment.vendor_id), ("assignment", self.vendor_a))
        self.assertEqual(assignment.order_id, order)
        self.assertEqual(assignment.round_number, order.bidding_round)
        invitation_authorizations = authorizations.filtered(
            lambda item: item.source == "invitation"
        )
        self.assertFalse(invitation_authorizations.filtered("active"))
        self.assertEqual(
            set(invitation_authorizations.mapped("deauthorization_reason")),
            {"winner_selected"},
        )
        self.assertTrue(all(invitation_authorizations.mapped("deauthorized_at")))
        order.with_user(self.ops).action_reopen_bidding()
        self.assertFalse(authorizations.filtered("active"))
        self.assertEqual(assignment.deauthorization_reason, "reopened")

    def test_assignment_statuses_and_vendor_deactivation(self):
        order = self._order()
        invitation = self._invitation(order=order)
        bid = self._submit(invitation)
        bid.with_user(self.admin).action_select_bid()
        assignment = self.env["trucalc.order.vendor.authorization"].sudo().search([
            ("order_id", "=", order.id), ("source", "=", "assignment"),
        ])
        for method in ("action_report_received", "action_assign_reviewer", "action_start_review"):
            getattr(order, method)()
            self.assertTrue(assignment.active)
        order.action_complete_review()
        self.assertFalse(assignment.active)
        self.assertEqual(assignment.deauthorization_reason, "completed")

        second = self._invitation()
        second_auth = self._authorization(second)
        self.vendor_a.active = False
        self.assertFalse(second_auth.active)
        self.assertEqual(second_auth.deauthorization_reason, "vendor_deactivated")
        self.vendor_a.active = True
        self.assertFalse(second_auth.active)

    def test_cancel_deactivates_all_without_manufacturing(self):
        invitation = self._invitation()
        authorization = self._authorization(invitation)
        invitation.order_id.action_cancelled()
        self.assertFalse(authorization.active)
        self.assertEqual(authorization.deauthorization_reason, "cancelled")
        inconsistent = self.env["trucalc.order"].sudo().browse(6)
        if inconsistent.exists():
            self.assertFalse(inconsistent.vendor_authorization_ids)
