import base64
from datetime import timedelta

from lxml import etree

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


PDF = base64.b64encode(b"%PDF-1.7\n4D3 controlled review\n%%EOF")


@tagged("post_install", "-at_install", "trucalc_review_workflow")
class TestControlledValuationReview(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({"name": "4D3 Other"})
        cls.admin = cls._user("4d3-admin", ["group_trucalc_admin"])
        cls.ops = cls._user("4d3-ops", ["group_trucalc_operations"])
        reviewer_groups = ["group_trucalc_operations", "group_trucalc_reviewer"]
        cls.reviewer = cls._user(
            "4d3-reviewer", reviewer_groups, cls.other_company,
        )
        cls.other_reviewer = cls._user(
            "4d3-other-reviewer", reviewer_groups, cls.other_company,
        )
        cls.vendor = cls.env["trucalc.vendor"].create({"name": "4D3 Vendor"})
        cls.env["trucalc.vendor.fee"].create({
            "vendor_id": cls.vendor.id, "service_type": "evaluation", "fee": 500,
        })
        cls.vendor_user = cls._user(
            "4d3-vendor", ["group_vendor_portal"], vendor=cls.vendor,
        )

    @classmethod
    def _user(cls, login, groups, company=False, vendor=False):
        company = company or cls.company
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login, "login": f"{login}@example.test",
            "email": f"{login}@example.test", "company_id": company.id,
            "company_ids": [Command.set(company.ids)],
            "group_ids": [Command.set([
                cls.env.ref(f"trucalc_orders.{group}").id for group in groups
            ])],
            "trucalc_vendor_id": vendor.id if vendor else False,
        })

    def _valuation_order(self):
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "4D3 Borrower", "property_address": "43 Review Way",
            "company_id": self.company.id, "service_type": "evaluation",
            "due_date": fields.Date.add(fields.Date.today(), days=14),
            "inspection_contact_name": "Review Contact",
            "inspection_contact_phone": "9015550103",
            "inspection_contact_email": "review@example.test",
        })
        order.with_user(self.admin).action_accept_request()
        order.with_user(self.admin).action_request_vendor_bids(
            self.vendor, fields.Datetime.now() + timedelta(days=2),
        )
        invitation = order.invitation_ids.filtered(
            lambda item: item.vendor_id == self.vendor
        )
        bid = invitation.with_user(self.vendor_user).action_vendor_submit_response(
            "standard_terms_accepted"
        )
        bid.with_user(self.ops)._action_confirm_engagement()
        self.env.flush_all()
        self.env["trucalc.vendor.order"].invalidate_model()
        projection = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user
        ).search([("order_number", "=", order.order_number)])
        projection.action_vendor_accept_engagement()
        authorization = order.vendor_authorization_ids.filtered(
            lambda item: item.active and item.source == "assignment"
        )
        valuation = self.env["trucalc.vendor.deliverable"].with_user(
            self.vendor_user
        )._submit(
            authorization, self.vendor_user, "valuation", "Initial.pdf", PDF,
        )
        return order, authorization, valuation

    def _assign_and_accept(self, order, reviewer=None):
        reviewer = reviewer or self.reviewer
        order.with_user(self.admin).action_assign_reviewer(reviewer)
        order.with_user(reviewer).action_start_review()

    def test_assignment_acceptance_and_direct_write_protection(self):
        order, _authorization, valuation = self._valuation_order()
        view = self.env["trucalc.order"].with_user(self.reviewer).get_view(
            view_id=self.env.ref("trucalc_orders.view_trucalc_order_form").id,
            view_type="form",
        )
        revision_button = etree.fromstring(view["arch"].encode()).xpath(
            "//button[@name='action_open_valuation_revision_wizard']"
        )
        self.assertEqual(len(revision_button), 1)
        self.assertEqual(revision_button[0].get("class"), "btn-outline-primary")
        with self.assertRaises(AccessError):
            order.with_user(self.admin).write({"reviewer_user_id": self.reviewer.id})
        order.with_user(self.admin).action_assign_reviewer(self.reviewer)
        self.assertEqual(order.status, "reviewer_assigned")
        self.assertEqual(order.reviewer_user_id, self.reviewer)
        assigned = order.lifecycle_event_ids.filtered(
            lambda event: event.event_type == "reviewer_assigned"
        )
        self.assertEqual(assigned.reviewer_user_id, self.reviewer)
        with self.assertRaises(AccessError):
            order.with_user(self.admin).action_start_review()
        self.assertEqual(order.status, "reviewer_assigned")
        self.assertEqual(order.reviewer_user_id, self.reviewer)
        self.assertFalse(order.lifecycle_event_ids.filtered(
            lambda event: event.event_type == "review_accepted"
        ))
        before_messages = order.message_ids
        with self.assertRaises(ValidationError):
            order.with_user(self.reviewer).action_request_valuation_revision(
                valuation, "Wrong state must not post.",
            )
        self.assertEqual(order.message_ids, before_messages)
        with self.assertRaises(AccessError):
            order.with_user(self.other_reviewer).action_start_review()
        order.with_user(self.reviewer).action_start_review()
        self.assertEqual(order.status, "under_review")
        self.assertEqual(len(order.lifecycle_event_ids.filtered(
            lambda event: event.event_type == "review_accepted"
        )), 1)
        with self.assertRaises(ValidationError):
            order.with_user(self.reviewer).action_start_review()

    def test_reassignment_requires_new_acceptance_and_preserves_request(self):
        order, authorization, valuation = self._valuation_order()
        order.with_user(self.admin).action_assign_reviewer(self.reviewer)
        order.with_user(self.ops).action_reassign_reviewer(
            self.other_reviewer, "Pre-acceptance coverage",
        )
        self.assertEqual(order.status, "reviewer_assigned")
        with self.assertRaises(AccessError):
            order.with_user(self.reviewer).action_start_review()
        order.with_user(self.other_reviewer).action_start_review()
        order.with_user(self.other_reviewer).action_request_valuation_revision(
            valuation, "Correct the certification page.",
        )
        order.with_user(self.admin).action_reassign_reviewer(
            self.reviewer, "Post-acceptance coverage",
        )
        self.assertEqual(order.status, "reviewer_assigned")
        self.assertTrue(self.env[
            "trucalc.order.lifecycle.event"
        ]._open_valuation_revision_request(valuation))
        revised = self.env["trucalc.vendor.deliverable"].with_user(
            self.vendor_user
        )._submit(
            authorization, self.vendor_user, "valuation", "Revised.pdf", PDF,
        )
        self.assertEqual((revised.version, order.status), (2, "reviewer_assigned"))
        order.with_user(self.reviewer).action_start_review()
        event = order.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "reviewer_reassigned"
            and item.reassignment_reason == "Post-acceptance coverage"
        )
        self.assertEqual(event.prior_reviewer_user_id, self.other_reviewer)
        self.assertEqual(event.reviewer_user_id, self.reviewer)
        self.assertEqual(event.reassignment_reason, "Post-acceptance coverage")

    def test_reviewer_access_revision_approval_and_indicator(self):
        order, authorization, first = self._valuation_order()
        invoice = self.env["trucalc.vendor.deliverable"].with_user(
            self.vendor_user
        )._submit(
            authorization, self.vendor_user, "vendor_invoice", "Invoice.pdf", PDF,
        )
        self._assign_and_accept(order)
        self.assertTrue(first.with_user(self.reviewer).has_access("read"))
        self.assertFalse(invoice.with_user(self.reviewer).has_access("read"))
        before_messages = order.message_ids
        with self.assertRaises(AccessError):
            order.with_user(self.other_reviewer).action_request_valuation_revision(
                first, "Unauthorized request must not post.",
            )
        self.assertEqual(order.message_ids, before_messages)
        order.with_user(self.reviewer).action_request_valuation_revision(
            first, "Revise the value conclusion.",
        )
        revision_events = order.lifecycle_event_ids.filtered(
            lambda event: event.event_type == "valuation_revision_requested"
        )
        self.assertEqual(len(revision_events), 1)
        self.assertEqual(revision_events.target_valuation_id, first)
        self.assertEqual(order.status, "under_review")
        revision_messages = order.message_ids - before_messages
        self.assertEqual(len(revision_messages), 1)
        self.assertIn("Valuation revision requested", revision_messages.body)
        self.assertIn("version 1", revision_messages.body)
        self.assertIn("Initial.pdf", revision_messages.body)
        before_rejected_messages = order.message_ids
        with self.assertRaises(ValidationError):
            order.with_user(self.reviewer).action_request_valuation_revision(
                first, "Duplicate request must not post.",
            )
        self.assertEqual(order.message_ids, before_rejected_messages)
        with self.assertRaises(ValidationError):
            order.with_user(self.reviewer).action_approve_valuation(first)
        self.env["trucalc.vendor.order"].invalidate_model()
        projection = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user
        ).search([("order_number", "=", order.order_number)])
        self.assertEqual(projection.review_indicator, "revision_request")
        second = self.env["trucalc.vendor.deliverable"].with_user(
            self.vendor_user
        )._submit(
            authorization, self.vendor_user, "valuation", "Revised.pdf", PDF,
        )
        self.env["trucalc.vendor.order"].invalidate_model()
        projection.invalidate_recordset()
        self.assertEqual(projection.review_indicator, "submitted")
        before_stale_messages = order.message_ids
        with self.assertRaises(ValidationError):
            order.with_user(self.reviewer).action_request_valuation_revision(
                first, "Stale target must not post.",
            )
        self.assertEqual(order.message_ids, before_stale_messages)
        with self.assertRaises(ValidationError):
            order.with_user(self.reviewer).action_approve_valuation(first)
        order.with_user(self.reviewer).action_approve_valuation(second)
        self.assertEqual(order.status, "under_review")
        approval = order.lifecycle_event_ids.filtered(
            lambda event: event.event_type == "valuation_approved"
        )
        self.assertEqual(approval.target_valuation_id, second)
        before_approved_messages = order.message_ids
        with self.assertRaises(ValidationError):
            order.with_user(self.reviewer).action_request_valuation_revision(
                second, "Approved target must not post.",
            )
        self.assertEqual(order.message_ids, before_approved_messages)
        with self.assertRaises(ValidationError):
            order.with_user(self.reviewer).action_approve_valuation(second)
        with self.assertRaises(ValidationError):
            order.with_user(self.admin).action_reassign_reviewer(
                self.other_reviewer, "Too late",
            )
