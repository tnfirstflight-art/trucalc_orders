import base64
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.trucalc_orders.models.bank_invoice import BankInvoice


PDF = base64.b64encode(b"%PDF-1.7\nSynthetic TC-00253 evidence\n%%EOF")


@tagged("post_install", "-at_install", "trucalc_post_acceptance_decline")
class TestPostAcceptanceDecline(TransactionCase):
    """Synthetic TC-00253: priced, bid, engaged, and Vendor-accepted."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Companies = cls.env["res.company"].with_context(
            trucalc_test_bank_fixture=True,
        )
        cls.bank = Companies.create({
            "name": "5B1 Synthetic TC-00253 Bank",
            "trucalc_is_bank": True,
            "trucalc_bank_active": True,
        })
        cls.admin = cls._user("admin", ["group_trucalc_admin"], scope=True)
        cls.ops = cls._user("ops", ["group_trucalc_operations"], scope=True)
        cls.reviewer = cls._user(
            "reviewer", ["group_trucalc_operations", "group_trucalc_reviewer"],
            scope=True,
        )
        cls.bank_user = cls._user(
            "bank", ["group_bank_admin"], bank=cls.bank,
        )
        cls.vendor = cls.env["trucalc.vendor"].create({
            "name": "5B1 Synthetic Joseph Newell",
        })
        cls.vendor_user = cls._user(
            "vendor", ["group_vendor_portal"], vendor=cls.vendor,
        )
        cls.env["trucalc.vendor.fee"].create({
            "vendor_id": cls.vendor.id,
            "service_type": "evaluation",
            "fee": 40,
        })
        cls.state = cls.env["res.country.state"].create({
            "name": "5B1 Synthetic Mississippi",
            "code": "Z5",
            "country_id": cls.env.ref("base.us").id,
        })
        cls.area = cls.env["trucalc.service.area"].with_user(cls.admin).create({
            "state_id": cls.state.id,
            "county": "Synthetic Pontotoc County",
            "service_type": "evaluation",
            "base_fee": 200,
        })
        cls.tag = cls.env["trucalc.document.tag"].with_user(cls.admin).create({
            "name": "5B1 Synthetic Inspection Support",
        })

    @classmethod
    def _user(
        cls, suffix, groups, bank=False, vendor=False, scope=False,
    ):
        companies = (cls.env.company | cls.bank) if scope else (
            bank if bank else cls.env.company
        )
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "5B1 " + suffix,
            "login": "5b1-" + suffix,
            "email": "5b1-%s@example.test" % suffix,
            "group_ids": [Command.set([
                cls.env.ref("trucalc_orders." + group).id for group in groups
            ])],
            "trucalc_bank_company_id": bank.id if bank else False,
            "trucalc_vendor_id": vendor.id if vendor else False,
            "company_id": bank.id if bank else cls.env.company.id,
            "company_ids": [Command.set(companies.ids)],
        })

    def _synthetic_tc00253(self):
        values = {
            "borrower": "Synthetic TC-00253 Borrower",
            "property_address": "253 Inspection Findings Road",
            "city": "Pontotoc",
            "zip_code": "38863",
            "loan_number": "SYNTHETIC-00253",
            "loan_purpose": "purchase",
            "service_type": "evaluation",
            "property_type": "single_family",
            "due_date": fields.Date.to_string(
                fields.Date.add(fields.Date.today(), days=4)
            ),
            "inspection_contact_name": "Synthetic Contact",
            "inspection_contact_phone": "6625550253",
            "inspection_contact_email": "inspection@example.test",
            "service_area_id": str(self.area.id),
            "service_state_id": str(self.area.state_id.id),
            "service_county": self.area.county,
        }
        bank_model = self.env["trucalc.order"].with_user(self.bank_user)
        order = bank_model._create_bank_draft(values, self.bank_user)
        legal = self.env["trucalc.document"]._create_bank_document(
            order, self.tag, "LEGAL.pdf", PDF, self.bank_user,
        )
        bank_model._send_bank_draft(order, values, self.bank_user)
        plat = self.env["trucalc.document"].with_user(self.admin).create({
            "order_id": order.id,
            "tag_id": self.tag.id,
            "filename": "Pontotoc_County_Illustrative_Plat.pdf",
            "attachment": PDF,
        })
        order.with_user(self.admin).action_accept_request()
        order.with_user(self.admin).action_request_vendor_bids(
            self.vendor, fields.Datetime.now() + timedelta(days=2),
        )
        invitation = order.invitation_ids
        bid = invitation.with_user(
            self.vendor_user
        ).action_vendor_submit_response("standard_terms_accepted")
        bid.with_user(self.ops)._action_confirm_engagement()
        authorization = order.vendor_authorization_ids.filtered(
            lambda item: item.active and item.source == "assignment"
        )
        self.env.flush_all()
        self.env["trucalc.vendor.order"].invalidate_model()
        projection = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user
        ).browse(authorization.id)
        projection.action_vendor_accept_engagement()
        return order, bid, authorization, order.current_engagement_id, legal | plat

    def _decline(self, order, actor=None):
        return order.with_user(actor or self.admin).action_decline_after_acceptance(
            "inspection_findings",
            "Inspection found conditions outside TruCalc acceptance criteria.",
            "Called the Vendor at 2:15 PM; Vendor confirmed work would stop.",
        )

    def test_synthetic_tc00253_decline_is_atomic_and_preserves_history(self):
        order, bid, assignment, engagement, documents = self._synthetic_tc00253()
        snapshot = {
            "round": order.bidding_round,
            "vendor": order.assigned_vendor_id,
            "vendor_fee": order.vendor_fee,
            "agreed_fee": order.agreed_fee,
            "current_fee": order.current_agreed_fee,
            "trucalc_fee": order.trucalc_fee,
            "bid": (bid.status, bid.bid_amount, bid.response_type),
            "documents": documents.ids,
            "invitation": (
                order.invitation_ids.state,
                order.invitation_ids.standard_fee,
            ),
        }
        invitation_authorization = self.env[
            "trucalc.order.vendor.authorization"
        ].sudo().with_context(active_test=False).search([
            ("order_id", "=", order.id), ("source", "=", "invitation"),
        ])
        before_deliverables = self.env[
            "trucalc.vendor.deliverable"
        ].sudo().search_count([("order_id", "=", order.id)])
        before_invoices = self.env["trucalc.bank.invoice"].sudo().search_count([
            ("order_id", "=", order.id),
        ])

        self.assertTrue(self._decline(order, self.ops))
        self.assertEqual(order.status, "declined")
        self.assertEqual(order.decline_category, "inspection_findings")
        self.assertIn("Inspection found", order.decline_reason)
        self.assertIn("Vendor confirmed", order.vendor_notification_note)
        self.assertEqual(order.declined_by_id, self.ops)
        self.assertTrue(order.declined_at)

        self.assertFalse(engagement.active)
        self.assertEqual(engagement.closed_reason, "order_declined")
        close_event = engagement.event_ids.filtered(
            lambda item: item.event_type == "engagement_closed_order_declined"
        )
        self.assertEqual(len(close_event), 1)
        self.assertEqual(close_event.actor_id, self.ops)
        self.assertEqual(close_event.reason, order.decline_reason)

        order.vendor_authorization_ids.invalidate_recordset()
        self.assertFalse(order.vendor_authorization_ids.filtered("active"))
        self.assertEqual(assignment.deauthorization_reason, "order_declined")
        self.assertEqual(
            invitation_authorization.deauthorization_reason, "winner_selected",
        )

        self.assertEqual(order.bidding_round, snapshot["round"])
        self.assertEqual(order.assigned_vendor_id, snapshot["vendor"])
        self.assertEqual(order.vendor_fee, snapshot["vendor_fee"])
        self.assertEqual(order.agreed_fee, snapshot["agreed_fee"])
        self.assertEqual(order.current_agreed_fee, snapshot["current_fee"])
        self.assertEqual(order.trucalc_fee, snapshot["trucalc_fee"])
        self.assertEqual(
            (bid.status, bid.bid_amount, bid.response_type), snapshot["bid"],
        )
        self.assertEqual(
            set(order.document_ids.ids), set(snapshot["documents"]),
        )
        self.assertEqual(
            (order.invitation_ids.state, order.invitation_ids.standard_fee),
            snapshot["invitation"],
        )
        self.assertEqual(
            self.env["trucalc.vendor.deliverable"].sudo().search_count([
                ("order_id", "=", order.id),
            ]),
            before_deliverables,
        )
        self.assertEqual(
            self.env["trucalc.bank.invoice"].sudo().search_count([
                ("order_id", "=", order.id),
            ]),
            before_invoices,
        )

        event = order.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "order_declined_after_acceptance"
        )
        self.assertEqual(len(event), 1)
        self.assertEqual((event.from_status, event.to_status), (
            "engaged", "declined",
        ))
        self.assertEqual(event.actor_id, self.ops)
        self.assertEqual(event.declined_vendor_id, self.vendor)
        self.assertEqual(event.declined_engagement_id, engagement)
        self.assertEqual(event.declined_vendor_fee, 40)
        self.assertEqual(event.decline_reason, order.decline_reason)
        self.assertEqual(
            event.vendor_notification_note, order.vendor_notification_note,
        )
        with self.assertRaises(AccessError):
            event.write({"decline_reason": "tampered"})
        with self.assertRaises(AccessError):
            event.unlink()

        chatter = " ".join(order.message_ids.mapped("body"))
        self.assertIn("Order declined after Vendor acceptance", chatter)
        self.assertIn("Manual Vendor Communication", chatter)
        self.env.flush_all()
        self.env["trucalc.vendor.order"].invalidate_model()
        self.assertFalse(self.env["trucalc.vendor.order"].with_user(
            self.vendor_user
        ).search([("order_number", "=", order.order_number)]))

    def test_authorization_state_and_required_evidence_fail_closed(self):
        for actor in (self.bank_user, self.vendor_user):
            order, _bid, assignment, engagement, _documents = (
                self._synthetic_tc00253()
            )
            with self.assertRaises(AccessError):
                self._decline(order, actor)
            self.assertEqual(order.status, "engaged")
            self.assertTrue(assignment.active)
            self.assertTrue(engagement.active)

        cases = (
            ("bad_category", "Reason", "Called Vendor"),
            ("inspection_findings", " ", "Called Vendor"),
            ("inspection_findings", "Reason", " "),
        )
        for category, reason, notification in cases:
            order, _bid, assignment, engagement, _documents = (
                self._synthetic_tc00253()
            )
            with self.assertRaises(ValidationError):
                order.with_user(self.admin).action_decline_after_acceptance(
                    category, reason, notification,
                )
            self.assertEqual(order.status, "engaged")
            self.assertTrue(assignment.active)
            self.assertTrue(engagement.active)

        new_order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "Not Engaged",
            "property_address": "1 Ineligible Way",
            "due_date": fields.Date.add(fields.Date.today(), days=5),
        })
        with self.assertRaises(ValidationError):
            self._decline(new_order)

    def test_wizard_requires_manual_notification_and_uses_controlled_action(self):
        order, _bid, _assignment, _engagement, _documents = (
            self._synthetic_tc00253()
        )
        action = order.with_user(
            self.admin
        ).action_open_post_acceptance_decline_wizard()
        self.assertEqual(
            action["res_model"],
            "trucalc.order.post.acceptance.decline.wizard",
        )
        wizard_model = self.env[
            "trucalc.order.post.acceptance.decline.wizard"
        ].with_user(self.admin)
        missing = wizard_model.create({
            "order_id": order.id,
            "category": "inspection_findings",
            "reason": "Inspection findings",
            "vendor_notification_note": " ",
        })
        with self.assertRaises(ValidationError):
            missing.action_decline()
        wizard = wizard_model.create({
            "order_id": order.id,
            "category": "inspection_findings",
            "reason": "Inspection findings require decline",
            "vendor_notification_note": "Called Vendor and confirmed stop-work.",
        })
        self.assertEqual(
            wizard.action_decline(), {"type": "ir.actions.act_window_close"},
        )
        self.assertEqual(order.status, "declined")

    def test_deliverable_and_bank_invoice_safeguards_are_atomic(self):
        order, _bid, assignment, engagement, _documents = self._synthetic_tc00253()
        deliverable = self.env["trucalc.vendor.deliverable"].with_user(
            self.vendor_user
        )._submit(
            assignment, self.vendor_user, "vendor_invoice", "Vendor Invoice.pdf", PDF,
        )
        with self.assertRaisesRegex(ValidationError, "Vendor Invoice"):
            self._decline(order)
        self.assertTrue(deliverable.exists())
        self.assertEqual(order.status, "engaged")
        self.assertTrue(assignment.active)
        self.assertTrue(engagement.active)

        bank_order, _bid, bank_assignment, bank_engagement, _documents = (
            self._synthetic_tc00253()
        )
        now = fields.Datetime.now()
        bank_invoice_model = self.env["trucalc.bank.invoice"].sudo()
        bank_invoice = super(BankInvoice, bank_invoice_model).create({
            "order_id": bank_order.id,
            "company_id": bank_order.company_id.id,
            "invoice_number": bank_order.order_number,
            "invoice_date": now.date(),
            "due_date": now.date() + timedelta(days=30),
            "amount": bank_order.current_agreed_fee,
            "currency_id": bank_order.fee_currency_id.id,
            "status": "issued",
            "issued_by": self.admin.id,
            "issued_at": now,
            "fee_workflow_revision": bank_order.fee_workflow_revision,
            "original_agreed_fee": bank_order.agreed_fee,
            "issuer_company_id": self.env.ref("base.main_company").id,
            "printed_values": {"fixture": "engaged-order safeguard"},
            "pdf_data": PDF,
            "pdf_filename": "%s.pdf" % bank_order.order_number,
        })
        with self.assertRaisesRegex(ValidationError, "Bank Invoice"):
            self._decline(bank_order)
        self.assertTrue(bank_invoice.exists())
        self.assertEqual(bank_order.status, "engaged")
        self.assertTrue(bank_assignment.active)
        self.assertTrue(bank_engagement.active)

        valuation_order, _bid, valuation_assignment, _engagement, _documents = (
            self._synthetic_tc00253()
        )
        valuation = self.env["trucalc.vendor.deliverable"].with_user(
            self.vendor_user
        )._submit(
            valuation_assignment, self.vendor_user,
            "valuation", "Valuation.pdf", PDF,
        )
        self.assertTrue(valuation.exists())
        with self.assertRaises(ValidationError):
            self._decline(valuation_order)

    def test_declined_and_cancelled_orders_reject_document_mutation(self):
        declined, _bid, _assignment, _engagement, documents = (
            self._synthetic_tc00253()
        )
        self._decline(declined)
        before = documents.ids
        with self.assertRaises(AccessError):
            self.env["trucalc.document"].with_user(self.admin).create({
                "order_id": declined.id,
                "tag_id": self.tag.id,
                "filename": "Late Internal.pdf",
                "attachment": PDF,
            })
        with self.assertRaises(AccessError):
            self.env["trucalc.document"]._create_bank_document(
                declined, self.tag, "Late Bank.pdf", PDF, self.bank_user,
            )
        with self.assertRaises(AccessError):
            documents[0].with_user(self.admin).write({
                "tag_id": self.tag.id,
            })
        self.assertEqual(set(declined.document_ids.ids), set(before))

        cancelled = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "Cancelled Document Guard",
            "property_address": "2 Terminal Way",
            "due_date": fields.Date.add(fields.Date.today(), days=5),
        })
        cancelled._controlled_lifecycle_write({"status": "cancelled"})
        with self.assertRaises(AccessError):
            self.env["trucalc.document"].with_user(self.admin).create({
                "order_id": cancelled.id,
                "tag_id": self.tag.id,
                "filename": "Cancelled Late.pdf",
                "attachment": PDF,
            })
