from datetime import timedelta

from lxml import etree

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_vendor_engagement_response")
class TestVendorEngagementResponse(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls._user("4c1-admin", "group_trucalc_admin")
        cls.ops = cls._user("4c1-ops", "group_trucalc_operations")
        cls.reviewer = cls._user("4c1-reviewer", "group_trucalc_reviewer")
        cls.bank_company = cls.env["res.company"].with_context(trucalc_test_bank_fixture=True).create({"name": "4C1 Bank", "trucalc_is_bank": True, "trucalc_bank_active": True})
        cls.bank = cls._user(
            "4c1-bank", "group_bank_admin", bank_company=cls.bank_company,
        )
        cls.vendor = cls.env["trucalc.vendor"].create({"name": "4C1 Vendor"})
        cls.other_vendor = cls.env["trucalc.vendor"].create({"name": "4C1 Other"})
        cls.env["trucalc.vendor.fee"].create([
            {"vendor_id": cls.vendor.id, "service_type": "evaluation", "fee": 0},
            {"vendor_id": cls.other_vendor.id, "service_type": "evaluation", "fee": 90},
        ])
        cls.vendor_user = cls._user(
            "4c1-vendor", "group_vendor_portal", vendor=cls.vendor,
        )
        cls.other_vendor_user = cls._user(
            "4c1-other-vendor", "group_vendor_portal", vendor=cls.other_vendor,
        )

    @classmethod
    def _user(cls, login, group, vendor=False, bank_company=False):
        groups = [group]
        if group == "group_trucalc_reviewer":
            groups.append("group_trucalc_operations")
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login,
            "login": login,
            "email": f"{login}@example.test",
            "group_ids": [Command.set([
                cls.env.ref(f"trucalc_orders.{name}").id for name in groups
            ])],
            "trucalc_vendor_id": vendor.id if vendor else False,
            "trucalc_bank_company_id": bank_company.id if bank_company else False,
            "company_id": bank_company.id if bank_company else cls.env.company.id,
            "company_ids": [Command.set(bank_company.ids if bank_company else cls.env.company.ids)],
        })

    def _engaged(self):
        delivery = fields.Date.add(fields.Date.today(), days=7)
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "4C1 Borrower",
            "property_address": "41 Response Lane",
            "company_id": self.env.company.id,
            "service_type": "evaluation",
            "due_date": delivery,
            "inspection_contact_name": "4C1 Inspection Contact",
            "inspection_contact_phone": "+1 901 555 0101 ext. 7",
            "inspection_contact_email": "inspection.4c1@example.test",
        })
        order.with_user(self.admin).action_accept_request()
        order.with_user(self.admin).action_request_vendor_bids(
            self.vendor, fields.Datetime.now() + timedelta(days=2),
        )
        invitation = order.invitation_ids
        bid = invitation.with_user(self.vendor_user).action_vendor_submit_response(
            "standard_terms_accepted"
        )
        bid.with_user(self.ops)._action_confirm_engagement()
        engagement = order.sudo().engagement_ids.filtered("active")
        self.env.flush_all()
        self.env["trucalc.vendor.order"].invalidate_model()
        projection = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user
        ).search([("order_number", "=", order.order_number)])
        return order, bid, engagement, projection

    def test_issuance_is_awaiting_with_strict_provenance_and_zero_fee(self):
        order, bid, engagement, projection = self._engaged()
        self.assertEqual(len(engagement), 1)
        self.assertEqual(engagement.response_state, "awaiting_acceptance")
        self.assertEqual(engagement.agreed_vendor_fee, 0)
        self.assertEqual(engagement.source_bid_id, bid)
        self.assertEqual(engagement.assignment_authorization_id.source, "assignment")
        self.assertEqual(engagement.source_engagement_audit_id.action, "vendor_engaged")
        self.assertEqual(engagement.vendor_delivery_date, order.vendor_delivery_date)
        self.assertFalse(engagement.event_ids)
        self.assertEqual(projection.engagement_response_label, "Awaiting Acceptance")
        self.assertFalse(projection.inspection_contact_name)
        self.assertFalse(projection.inspection_contact_phone)
        self.assertFalse(projection.inspection_contact_email)
        self.assertTrue(projection.can_accept_engagement)
        self.assertNotIn("due_date", projection.fields_get())
        self.assertFalse(order.engagement_action_required)
        self.assertNotIn(order, self.env["trucalc.order"].with_user(self.admin).search([
            ("engagement_action_required", "=", True),
        ]))

    def test_direct_accept_is_final_immutable_and_vendor_attributed(self):
        order, _bid, engagement, projection = self._engaged()
        event = projection.action_vendor_accept_engagement()
        self.env.flush_all()
        projection.invalidate_recordset()
        self.assertEqual(engagement.response_state, "accepted")
        self.assertEqual(engagement.acceptance_mode, "direct_vendor")
        self.assertEqual(engagement.vendor_accepted_by_id, self.vendor_user)
        self.assertFalse(engagement.delivery_change_approved_by_id)
        self.assertEqual((event.event_type, event.actor_id), ("vendor_accepted", self.vendor_user))
        self.assertEqual(event.agreed_vendor_fee, 0)
        self.assertEqual(order.vendor_delivery_date, engagement.vendor_delivery_date)
        self.assertEqual(projection.inspection_contact_name, "4C1 Inspection Contact")
        self.assertEqual(
            projection.inspection_contact_phone, "9015550101 ext 7",
        )
        self.assertEqual(
            projection.inspection_contact_phone_display,
            "(901) 555-0101 ext. 7",
        )
        self.assertEqual(projection.inspection_contact_email, "inspection.4c1@example.test")
        self.assertFalse(order.engagement_action_required)
        with self.assertRaises(ValidationError):
            projection.action_vendor_decline_engagement("Too late")
        with self.assertRaises(AccessError):
            engagement.with_user(self.admin).write({"response_state": "declined"})
        with self.assertRaises(AccessError):
            event.with_user(self.admin).unlink()

    def test_request_reject_repeat_and_approve_preserve_actor_semantics(self):
        order, _bid, engagement, projection = self._engaged()
        original = order.vendor_delivery_date
        first_date = fields.Date.add(original, days=2)
        with self.assertRaises(ValidationError):
            projection.action_vendor_request_delivery_change(original, "Not later")
        request1 = projection.action_vendor_request_delivery_change(
            first_date, "Vendor scheduling constraint",
        )
        self.assertEqual(engagement.response_state, "delivery_change_requested")
        projection.invalidate_recordset()
        self.assertFalse(projection.inspection_contact_name)
        self.assertTrue(order.engagement_action_required)
        self.assertEqual(
            order.engagement_action_required_reason, "delivery_change_requested"
        )
        self.assertIn(order, self.env["trucalc.order"].with_user(self.ops).search([
            ("engagement_action_required", "=", True),
        ]))
        self.assertEqual(request1.actor_id, self.vendor_user)
        self.assertEqual(order.vendor_delivery_date, original)
        with self.assertRaises(ValidationError):
            projection.action_vendor_request_delivery_change(
                fields.Date.add(first_date, days=1), "Second pending request",
            )
        with self.assertRaises(ValidationError):
            engagement.with_user(self.ops).action_reject_delivery_change("  ")
        rejection = engagement.with_user(self.ops).action_reject_delivery_change(
            "Original commitment required"
        )
        self.assertEqual(rejection.prior_event_id, request1)
        self.assertEqual(rejection.actor_id, self.ops)
        self.assertEqual(engagement.response_state, "awaiting_acceptance")
        self.assertFalse(order.engagement_action_required)

        second_date = fields.Date.add(first_date, days=2)
        request2 = projection.action_vendor_request_delivery_change(
            second_date, "Revised achievable commitment",
        )
        approval = engagement.with_user(self.admin).action_approve_delivery_change(
            "Approved operationally"
        )
        self.assertEqual(approval.prior_event_id, request2)
        self.assertEqual(approval.actor_id, self.admin)
        self.assertEqual(engagement.response_state, "accepted")
        self.env.flush_all()
        projection.invalidate_recordset()
        self.assertEqual(projection.inspection_contact_name, "4C1 Inspection Contact")
        self.assertEqual(engagement.acceptance_mode, "delivery_change_approved")
        self.assertFalse(engagement.vendor_accepted_by_id)
        self.assertEqual(engagement.conditional_request_event_id, request2)
        self.assertEqual(engagement.delivery_change_approved_by_id, self.admin)
        self.assertEqual(request2.actor_id, self.vendor_user)
        self.assertEqual(order.vendor_delivery_date, second_date)
        self.assertEqual(engagement.vendor_delivery_date, second_date)
        self.assertEqual(engagement.agreed_vendor_fee, 0)
        self.assertFalse(order.engagement_action_required)

    def test_decline_requires_reason_keeps_assignment_authorization_active(self):
        order, _bid, engagement, projection = self._engaged()
        with self.assertRaises(ValidationError):
            projection.action_vendor_decline_engagement(" ")
        event = projection.action_vendor_decline_engagement("Cannot perform")
        projection.invalidate_recordset()
        self.assertEqual((engagement.response_state, event.event_type), (
            "declined", "vendor_declined",
        ))
        self.assertEqual(engagement.declined_by_id, self.vendor_user)
        self.assertTrue(engagement.assignment_authorization_id.active)
        self.assertFalse(projection.inspection_contact_name)
        self.assertEqual(order.status, "engaged")
        self.assertTrue(order.engagement_action_required)
        self.assertEqual(order.engagement_action_required_reason, "declined")
        self.env.flush_all()
        projection.invalidate_recordset()
        self.assertFalse(projection.can_accept_engagement)

    def test_security_rejects_external_personas_and_direct_crud(self):
        _order, _bid, engagement, projection = self._engaged()
        with self.assertRaises(AccessError):
            projection.with_user(self.other_vendor_user).action_vendor_accept_engagement()
        with self.assertRaises(AccessError):
            engagement.with_user(self.bank).action_approve_delivery_change()
        with self.assertRaises(AccessError):
            engagement.with_user(self.vendor_user).action_approve_delivery_change()
        with self.assertRaises(AccessError):
            self.env["trucalc.vendor.engagement"].with_user(self.admin).create({})
        with self.assertRaises(AccessError):
            self.env["trucalc.vendor.engagement.event"].with_user(self.admin).create({})

    def test_reopen_closes_engagement_and_removes_vendor_projection(self):
        order, _bid, engagement, projection = self._engaged()
        projection.action_vendor_decline_engagement("Reopen needed")
        order.with_user(self.admin).action_reopen_bidding()
        self.assertFalse(engagement.active)
        self.assertEqual(engagement.closed_reason, "reopened")
        self.assertFalse(order.engagement_action_required)
        self.assertEqual(
            engagement.event_ids.sorted("id")[-1].event_type,
            "engagement_closed_reopened",
        )
        self.assertFalse(engagement.assignment_authorization_id.active)
        self.env.flush_all()
        self.env["trucalc.vendor.order"].invalidate_model()
        self.assertFalse(self.env["trucalc.vendor.order"].with_user(
            self.vendor_user
        ).search([("order_number", "=", order.order_number)]))
        with self.assertRaises(AccessError):
            projection.action_vendor_accept_engagement()

    def test_action_required_list_form_and_search_views(self):
        list_arch = etree.fromstring(self.env.ref(
            "trucalc_orders.view_trucalc_order_list"
        ).arch_db.encode())
        badge = list_arch.xpath(
            "//field[@name='engagement_action_required_label' and @widget='badge']"
        )
        self.assertEqual(len(badge), 1)
        self.assertEqual(badge[0].get("decoration-danger"), "engagement_action_required")
        self.assertEqual(len(list_arch.xpath(
            "//field[@name='engagement_action_required_reason']"
        )), 1)

        form_arch = etree.fromstring(self.env.ref(
            "trucalc_orders.view_trucalc_order_form"
        ).arch_db.encode())
        self.assertEqual(len(form_arch.xpath(
            "//div[contains(@class, 'alert-danger')]"
            "//span[contains(@class, 'badge') and normalize-space()='Action Required']"
        )), 1)
        self.assertEqual(len(form_arch.xpath(
            "//div[contains(@class, 'alert-danger')]"
            "//field[@name='engagement_action_required_reason']"
        )), 1)

        search_arch = etree.fromstring(self.env.ref(
            "trucalc_orders.view_trucalc_order_search"
        ).arch_db.encode())
        action_filter = search_arch.xpath(
            "//filter[@name='action_required' and @string='Action Required']"
        )
        self.assertEqual(len(action_filter), 1)
        self.assertIn("engagement_action_required", action_filter[0].get("domain"))
