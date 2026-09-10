from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.trucalc_orders.models.vendor_order_authorization import (
    TruCalcOrderVendorAuthorization,
)
from odoo.addons.trucalc_orders.models.bid_audit import TruCalcBidAudit


@tagged("post_install", "-at_install", "trucalc_vendor_engagement")
class TestVendorEngagement(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls._user("4c0-admin", "group_trucalc_admin")
        cls.ops = cls._user("4c0-ops", "group_trucalc_operations")
        cls.reviewer = cls._user("4c0-reviewer", "group_trucalc_reviewer")
        cls.generic = cls._user("4c0-generic", None)
        cls.bank_company = cls.env["res.company"].with_context(trucalc_test_bank_fixture=True).create({"name": "4C0 Bank", "trucalc_is_bank": True, "trucalc_bank_active": True})
        cls.bank = cls._user(
            "4c0-bank", "group_bank_admin", bank_company=cls.bank_company,
        )
        cls.vendor_a = cls.env["trucalc.vendor"].create({"name": "4C0 Vendor A"})
        cls.vendor_b = cls.env["trucalc.vendor"].create({"name": "4C0 Vendor B"})
        cls.env["trucalc.vendor.fee"].create([
            {"vendor_id": cls.vendor_a.id, "service_type": "evaluation", "fee": 0},
            {"vendor_id": cls.vendor_b.id, "service_type": "evaluation", "fee": 125},
        ])
        cls.vendor_user_a = cls._user(
            "4c0-vendor-a", "group_vendor_portal", vendor=cls.vendor_a,
        )
        cls.vendor_user_b = cls._user(
            "4c0-vendor-b", "group_vendor_portal", vendor=cls.vendor_b,
        )

    @classmethod
    def _user(cls, login, group, vendor=False, bank_company=False):
        groups = [cls.env.ref(f"trucalc_orders.{group}").id] if group else [
            cls.env.ref("base.group_user").id
        ]
        if group == "group_trucalc_reviewer":
            groups.append(cls.env.ref(
                "trucalc_orders.group_trucalc_operations"
            ).id)
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login, "login": login, "email": f"{login}@example.test",
            "group_ids": [Command.set(groups)],
            "trucalc_vendor_id": vendor.id if vendor else False,
            "trucalc_bank_company_id": bank_company.id if bank_company else False,
            "company_id": bank_company.id if bank_company else cls.env.company.id,
            "company_ids": [Command.set(bank_company.ids if bank_company else cls.env.company.ids)],
        })

    def _order_with_responses(self):
        due = fields.Date.add(fields.Date.today(), days=14)
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "4C0 Borrower", "property_address": "4 Engagement Way",
            "company_id": self.env.company.id, "service_type": "evaluation",
            "due_date": due,
        })
        order.with_user(self.admin).action_accept_request()
        deadline = fields.Datetime.now() + timedelta(days=2)
        order.with_user(self.admin).action_request_vendor_bids(
            self.vendor_a | self.vendor_b, deadline,
        )
        invitation_a = order.invitation_ids.filtered(
            lambda item: item.vendor_id == self.vendor_a
        )
        invitation_b = order.invitation_ids.filtered(
            lambda item: item.vendor_id == self.vendor_b
        )
        bid_a = invitation_a.with_user(self.vendor_user_a).action_vendor_submit_response(
            "standard_terms_accepted"
        )
        proposed = fields.Date.add(due, days=1)
        bid_b = invitation_b.with_user(self.vendor_user_b).action_vendor_submit_response(
            "fee_and_delivery_counter", proposed_fee=150,
            proposed_delivery_date=proposed,
        )
        return order, bid_a, bid_b

    def _wizard(self, bid, user):
        action = bid.with_user(user).action_select_bid()
        return self.env[action["res_model"]].with_user(user).create({"bid_id": bid.id})

    def test_open_cancel_dismiss_are_read_only_and_roles_are_enforced(self):
        order, bid, other = self._order_with_responses()
        initial = (
            order.status, order.assigned_vendor_id, order.vendor_fee,
            order.vendor_delivery_date, order.vendor_engaged_at,
            bid.status, other.status,
            tuple(order.invitation_ids.mapped("state")),
            tuple(order.sudo().vendor_authorization_ids.mapped("active")),
            len(order.message_ids),
        )
        for user in (self.admin, self.ops):
            wizard = self._wizard(bid, user)
            self.assertEqual(wizard.order_number, order.order_number)
            self.assertEqual(wizard.vendor_id, self.vendor_a)
            self.assertEqual(wizard.accepted_fee, 0)
            self.assertEqual(
                wizard.vendor_delivery_date_display,
                bid.proposed_delivery_date.strftime("%m/%d/%Y"),
            )
            wizard.unlink()  # equivalent persisted effect to cancel/dismiss/abandon
            self.assertEqual(
                initial,
                (order.status, order.assigned_vendor_id, order.vendor_fee,
                 order.vendor_delivery_date, order.vendor_engaged_at,
                 bid.status, other.status,
                 tuple(order.invitation_ids.mapped("state")),
                 tuple(order.sudo().vendor_authorization_ids.mapped("active")),
                 len(order.message_ids)),
            )
        for user in (
            self.bank, self.vendor_user_a, self.generic,
        ):
            with self.assertRaises(AccessError):
                bid.with_user(user).action_select_bid()
            with self.assertRaises(AccessError):
                bid.with_user(user)._action_confirm_engagement()

    def test_confirm_engages_atomically_and_accepts_zero_fee(self):
        order, bid, other = self._order_with_responses()
        due = order.due_date
        wizard = self._wizard(bid, self.ops)
        wizard.action_engage_vendor()
        self.assertEqual(order.status, "engaged")
        self.assertEqual(order.assigned_vendor_id, self.vendor_a)
        self.assertEqual(order.vendor_fee, 0)
        self.assertEqual(order.vendor_delivery_date, bid.proposed_delivery_date)
        self.assertEqual(order.due_date, due)
        self.assertTrue(order.vendor_engaged_at)
        self.assertEqual((bid.status, other.status), ("selected", "not_selected"))
        self.assertEqual(set(order.invitation_ids.mapped("state")), {"closed"})
        active = order.sudo().vendor_authorization_ids.filtered("active")
        self.assertEqual(len(active), 1)
        self.assertEqual((active.source, active.vendor_id), ("assignment", self.vendor_a))
        audit = self.env["trucalc.bid.audit"].search([
            ("order_id", "=", order.id), ("action", "=", "vendor_engaged"),
        ])
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit.actor_id, self.ops)
        self.assertEqual(
            audit.new_values["vendor_engaged_at"],
            fields.Datetime.to_string(order.vendor_engaged_at),
        )

    def test_engagement_uses_one_authoritative_timestamp(self):
        order, bid, _other = self._order_with_responses()
        logged_events = []
        original_log_event = TruCalcBidAudit._log_event

        def capture_log_event(audit_model, action, event_order, **values):
            logged_events.append((action, values.get("event_at")))
            return original_log_event(audit_model, action, event_order, **values)

        with patch.object(TruCalcBidAudit, "_log_event", capture_log_event):
            bid.with_user(self.ops)._action_confirm_engagement()

        audit = self.env["trucalc.bid.audit"].search([
            ("order_id", "=", order.id), ("action", "=", "vendor_engaged"),
        ])
        engagement = order.sudo().engagement_ids.filtered("active")
        authorization = engagement.assignment_authorization_id
        self.assertEqual(logged_events, [("vendor_engaged", order.vendor_engaged_at)])
        self.assertEqual(order.vendor_engaged_at, audit.event_at)
        self.assertEqual(audit.event_at, engagement.engaged_at)
        self.assertEqual(engagement.order_id, order)
        self.assertEqual(engagement.company_id, order.company_id)
        self.assertEqual(engagement.vendor_id, order.assigned_vendor_id)
        self.assertEqual(engagement.round_number, order.bidding_round)
        self.assertEqual(engagement.source_bid_id, bid)
        self.assertEqual(bid.status, "selected")
        self.assertEqual(authorization.order_id, order)
        self.assertEqual(authorization.vendor_id, engagement.vendor_id)
        self.assertEqual(authorization.company_id, engagement.company_id)
        self.assertEqual(authorization.round_number, engagement.round_number)
        self.assertEqual(authorization.source, "assignment")
        self.assertEqual(audit.order_id, order)
        self.assertEqual(audit.bid_id, bid)
        self.assertEqual(audit.action, "vendor_engaged")
        self.assertEqual(audit.actor_id, engagement.engaged_by_id)

    def test_confirmation_revalidates_deadline_and_authorization(self):
        order, bid, _other = self._order_with_responses()
        wizard = self._wizard(bid, self.admin)
        bid.invitation_id.with_user(self.admin).action_set_response_deadline(
            fields.Datetime.now() - timedelta(seconds=1)
        )
        with self.assertRaises(ValidationError):
            wizard.action_engage_vendor()
        self.assertEqual((order.status, bid.status), ("bid_requested", "submitted"))

        order2, bid2, _other2 = self._order_with_responses()
        wizard2 = self._wizard(bid2, self.admin)
        self.env["trucalc.order.vendor.authorization"]._deactivate(
            [("invitation_id", "=", bid2.invitation_id.id)], "revoked"
        )
        with self.assertRaises(ValidationError):
            wizard2.action_engage_vendor()
        self.assertEqual((order2.status, bid2.status), ("bid_requested", "submitted"))

    def test_noncanonical_legacy_response_cannot_open_confirmation(self):
        legacy = self.env["trucalc.bid"].sudo().browse(11).exists()
        if legacy:
            with self.assertRaises(ValidationError):
                legacy.with_user(self.admin).action_select_bid()

    def test_failure_rolls_back_every_engagement_mutation(self):
        order, bid, other = self._order_with_responses()
        initial_messages = set(order.message_ids.ids)

        def fail_assignment(_model, _order, _vendor, _round_number):
            raise ValidationError("Injected assignment authorization failure")

        try:
            with self.env.cr.savepoint(), patch.object(
                TruCalcOrderVendorAuthorization,
                "_create_for_assignment",
                fail_assignment,
            ):
                bid.with_user(self.admin)._action_confirm_engagement()
        except ValidationError:
            pass
        else:
            self.fail("The injected engagement failure did not occur")
        self.env.invalidate_all()
        self.assertEqual(order.status, "bid_requested")
        self.assertFalse(order.assigned_vendor_id)
        self.assertFalse(order.vendor_engaged_at)
        self.assertEqual((bid.status, other.status), ("submitted", "submitted"))
        self.assertEqual(set(order.invitation_ids.mapped("state")), {"invited"})
        self.assertTrue(all(order.sudo().vendor_authorization_ids.mapped("active")))
        self.assertEqual(set(order.message_ids.ids), initial_messages)
        self.assertFalse(self.env["trucalc.bid.audit"].search([
            ("order_id", "=", order.id), ("action", "=", "vendor_engaged"),
        ]))

    def test_engagement_datetime_is_server_controlled(self):
        order, _bid, _other = self._order_with_responses()
        with self.assertRaises(AccessError):
            order.write({"vendor_engaged_at": fields.Datetime.now()})
        with self.assertRaises(AccessError):
            self.env["trucalc.order"].with_user(self.admin).create({
                "borrower": "Forged", "property_address": "No",
                "due_date": fields.Date.add(fields.Date.today(), days=1),
                "vendor_engaged_at": fields.Datetime.now(),
            })
