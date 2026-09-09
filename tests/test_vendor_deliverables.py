import base64
import unittest
from datetime import timedelta

from lxml import etree

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests import HttpCase
from odoo import http


PDF = base64.b64encode(b"%PDF-1.7\nTruCalc controlled test PDF\n%%EOF")


@tagged("post_install", "-at_install", "trucalc_vendor_deliverables")
class TestVendorDeliverables(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({"name": "4D2 Other"})
        cls.admin = cls._user("4d2-admin", "group_trucalc_admin")
        cls.ops = cls._user("4d2-ops", "group_trucalc_operations")
        reviewer_groups = ["group_trucalc_operations", "group_trucalc_reviewer"]
        cls.reviewer = cls._user("4d2-reviewer", reviewer_groups)
        cls.reviewer.sudo().write({
            "company_id": cls.other_company.id,
            "company_ids": [Command.set([cls.other_company.id])],
        })
        cls.other_reviewer = cls._user(
            "4d2-other-reviewer", reviewer_groups,
        )
        cls.other_reviewer.sudo().write({
            "company_id": cls.other_company.id,
            "company_ids": [Command.set([cls.other_company.id])],
        })
        cls.operations_reviewer = cls._user(
            "4d2-operations-reviewer", reviewer_groups,
        )
        cls.admin_reviewer = cls._user(
            "4d2-admin-reviewer",
            ["group_trucalc_admin", "group_trucalc_reviewer"],
        )
        cls.bank = cls._user(
            "4d2-bank", "group_bank_admin", bank=cls.company,
        )
        cls.vendor = cls.env["trucalc.vendor"].create({"name": "4D2 Vendor"})
        cls.other_vendor = cls.env["trucalc.vendor"].create({"name": "4D2 Other Vendor"})
        cls.env["trucalc.vendor.fee"].create([
            {"vendor_id": cls.vendor.id, "service_type": "evaluation", "fee": 500},
            {"vendor_id": cls.other_vendor.id, "service_type": "evaluation", "fee": 600},
        ])
        cls.vendor_user = cls._user(
            "4d2-vendor", "group_vendor_portal", vendor=cls.vendor,
        )
        cls.other_vendor_user = cls._user(
            "4d2-other-vendor", "group_vendor_portal", vendor=cls.other_vendor,
        )

    @classmethod
    def _user(cls, login, group, vendor=False, bank=False):
        groups = group if isinstance(group, (list, tuple)) else [group]
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login, "login": login, "email": f"{login}@example.test",
            "group_ids": [Command.set([
                cls.env.ref(f"trucalc_orders.{group_name}").id
                for group_name in groups
            ])],
            "trucalc_vendor_id": vendor.id if vendor else False,
            "trucalc_bank_company_id": bank.id if bank else False,
        })

    def _engaged(self, vendors=None, accept=True, delivery_date=None):
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "4D2 Borrower",
            "property_address": "42 Deliverable Lane",
            "company_id": self.company.id,
            "service_type": "evaluation",
            "due_date": delivery_date or fields.Date.add(fields.Date.today(), days=14),
            "inspection_contact_name": "4D2 Contact",
            "inspection_contact_phone": "9015550102",
            "inspection_contact_email": "contact.4d2@example.test",
        })
        order.with_user(self.admin).action_accept_request()
        order.with_user(self.admin).action_request_vendor_bids(
            vendors or self.vendor, fields.Datetime.now() + timedelta(days=2),
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
        if accept:
            projection.action_vendor_accept_engagement()
        authorization = order.vendor_authorization_ids.filtered(
            lambda item: item.active and item.source == "assignment"
        )
        return order, authorization, projection

    def _submit(self, authorization, artifact_type="valuation",
                filename="Valuation.pdf", actor=None, data=PDF):
        actor = actor or self.vendor_user
        return self.env["trucalc.vendor.deliverable"].with_user(actor)._submit(
            authorization, actor, artifact_type, filename, data,
        )

    def _request_revision(self, order, valuation, instructions="Correct the value conclusion."):
        return self.env["trucalc.order.lifecycle.event"].with_user(
            self.admin
        )._request_valuation_revision(
            order, valuation, instructions, self.admin,
        )

    def test_first_valuation_is_authoritative_current_and_receives_report(self):
        order, authorization, projection = self._engaged()
        deliverable = self._submit(authorization)
        self.assertEqual((deliverable.version, deliverable.is_current), (1, True))
        self.assertEqual(deliverable.status, "submitted")
        self.assertEqual(deliverable.order_id, order)
        self.assertEqual(deliverable.company_id, order.company_id)
        self.assertEqual(deliverable.vendor_id, self.vendor)
        self.assertEqual(deliverable.submitted_by_id, self.vendor_user)
        self.assertEqual(deliverable.authorization_id, authorization)
        self.assertEqual(deliverable.engagement_id, order.current_engagement_id)
        self.assertEqual(deliverable.bidding_round, order.bidding_round)
        self.assertTrue(deliverable.submitted_at)
        self.assertEqual(order.status, "report_received")
        event = order.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "valuation_received"
        )
        self.assertEqual(len(event), 1)
        self.assertEqual((event.from_status, event.to_status), (
            "engaged", "report_received",
        ))
        self.assertEqual(event.actor_id, self.vendor_user)
        self.assertEqual(event.deliverable_id, deliverable)
        self.env.flush_all()
        self.env["trucalc.vendor.order"].invalidate_model()
        projection = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user
        ).search([("order_number", "=", order.order_number)])
        self.assertFalse(projection.inspection_contact_name)

    def test_pdf_filename_signature_size_and_version_are_server_controlled(self):
        for filename, payload, message in (
            ("Valuation.docx", PDF, "PDF filename"),
            ("bad/name.pdf", PDF, "Filename may contain"),
            ("Valuation.pdf", base64.b64encode(b"not pdf"), "PDF signature"),
        ):
            _order, authorization, _projection = self._engaged()
            with self.assertRaisesRegex(ValidationError, message):
                self._submit(authorization, filename=filename, data=payload)
        model = self.env["trucalc.vendor.deliverable"].with_user(self.vendor_user)
        with self.assertRaisesRegex(ValidationError, "50 MB"):
            model._validate_pdf(
                "Large.pdf",
                base64.b64encode(b"%PDF-" + b"x" * (50 * 1024 * 1024)),
            )

    def test_direct_crud_and_all_authoritative_fields_are_immutable(self):
        order, authorization, _projection = self._engaged()
        deliverable = self._submit(authorization)
        with self.assertRaises(AccessError):
            self.env["trucalc.vendor.deliverable"].with_user(self.admin).create({})
        for values in (
            {"filename": "Changed.pdf"}, {"file_data": PDF}, {"version": 2},
            {"vendor_id": self.other_vendor.id}, {"order_id": order.id},
            {"is_current": False}, {"status": "submitted"},
        ):
            with self.assertRaises(AccessError):
                deliverable.with_user(self.admin).write(values)
        with self.assertRaises(AccessError):
            deliverable.with_user(self.admin).unlink()

    def test_order_company_change_cannot_mutate_downstream_provenance(self):
        order, authorization, _projection = self._engaged()
        valuation = self._submit(authorization)
        invoice = self._submit(
            authorization, "vendor_invoice", "Immutable Company Invoice.pdf",
        )
        order = order.sudo()
        authorization = authorization.sudo()
        engagement = order.current_engagement_id.sudo()
        valuation = valuation.sudo()
        invoice = invoice.sudo()
        lifecycle_event = order.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "valuation_received"
        ).sudo()
        before = {
            "order": order.company_id,
            "authorization": authorization.company_id,
            "engagement": engagement.company_id,
            "valuation": valuation.company_id,
            "invoice": invoice.company_id,
            "lifecycle_event": lifecycle_event.company_id,
        }

        for actor in (self.admin, self.ops):
            with self.subTest(actor=actor.login), self.assertRaises(AccessError):
                order.with_user(actor).write({
                    "company_id": self.other_company.id,
                })

        self.env.flush_all()
        for record in (
            order, authorization, engagement, valuation, invoice, lifecycle_event,
        ):
            record.invalidate_recordset(["company_id"])
        self.assertEqual(order.company_id, before["order"])
        self.assertEqual(authorization.company_id, before["authorization"])
        self.assertEqual(engagement.company_id, before["engagement"])
        self.assertEqual(valuation.company_id, before["valuation"])
        self.assertEqual(invoice.company_id, before["invoice"])
        self.assertEqual(lifecycle_event.company_id, before["lifecycle_event"])

    def test_replay_has_no_duplicate_event_or_orphan_attachment(self):
        order, authorization, _projection = self._engaged()
        deliverable = self._submit(authorization)
        attachment_domain = [
            ("res_model", "=", "trucalc.vendor.deliverable"),
            ("res_id", "=", deliverable.id),
        ]
        before = self.env["ir.attachment"].sudo().search_count(attachment_domain)
        with self.assertRaises(AccessError):
            self._submit(authorization, filename="Replay.pdf")
        self.assertEqual(self.env["trucalc.vendor.deliverable"].sudo().search_count([
            ("order_id", "=", order.id), ("artifact_type", "=", "valuation"),
        ]), 1)
        self.assertEqual(self.env["ir.attachment"].sudo().search_count(
            attachment_domain
        ), before)
        self.assertEqual(len(order.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "valuation_received"
        )), 1)

    def test_revision_request_is_exact_required_immutable_and_single_use(self):
        order, authorization, _projection = self._engaged()
        valuation = self._submit(authorization)
        invoice = self._submit(
            authorization, "vendor_invoice", "Revision Invoice.pdf",
        )
        Event = self.env["trucalc.order.lifecycle.event"].with_user(self.admin)
        for instructions in (False, "   "):
            with self.assertRaisesRegex(ValidationError, "instructions are required"):
                Event._request_valuation_revision(
                    order, valuation, instructions, self.admin,
                )
        with self.assertRaisesRegex(ValidationError, "no longer eligible"):
            Event._request_valuation_revision(
                order, invoice, "Not an invoice workflow", self.admin,
            )
        request = self._request_revision(order, valuation, "  Correct page 4.  ")
        self.assertEqual(request.target_valuation_id, valuation)
        self.assertEqual(request.vendor_revision_instructions, "Correct page 4.")
        self.assertEqual((request.from_status, request.to_status), (
            "report_received", "report_received",
        ))
        with self.assertRaisesRegex(ValidationError, "already has a revision request"):
            self._request_revision(order, valuation)
        with self.assertRaises(AccessError):
            request.with_user(self.admin).write({
                "vendor_revision_instructions": "Changed",
            })
        with self.assertRaises(AccessError):
            request.with_user(self.admin).unlink()

    def test_revised_valuations_are_sequential_current_and_audited(self):
        order, authorization, _projection = self._engaged()
        first = self._submit(authorization, filename="Version 1.pdf")
        original = {
            "filename": first.filename,
            "file_data": first.file_data,
            "submitted_at": first.submitted_at,
            "submitted_by_id": first.submitted_by_id,
            "vendor_id": first.vendor_id,
            "engagement_id": first.engagement_id,
            "authorization_id": first.authorization_id,
        }
        with self.assertRaisesRegex(AccessError, "open revision request"):
            self._submit(authorization, filename="Unauthorized Version 2.pdf")
        request_one = self._request_revision(order, first, "Revise the first report.")
        second = self._submit(authorization, filename="Version 2.pdf")
        first.invalidate_recordset()
        self.assertEqual(order.status, "report_received")
        self.assertEqual((first.is_current, second.version, second.is_current), (
            False, 2, True,
        ))
        self.assertFalse(self.env[
            "trucalc.order.lifecycle.event"
        ]._open_valuation_revision_request(first))
        submitted_one = order.lifecycle_event_ids.filtered(
            lambda event: event.event_type == "valuation_revision_submitted"
        )
        self.assertEqual(submitted_one.revision_request_event_id, request_one)
        self.assertEqual(submitted_one.target_valuation_id, first)
        self.assertEqual(submitted_one.new_valuation_id, second)
        request_two = self._request_revision(order, second, "Revise the second report.")
        third = self._submit(authorization, filename="Version 3.pdf")
        second.invalidate_recordset()
        self.assertEqual((second.is_current, third.version, third.is_current), (
            False, 3, True,
        ))
        current = self.env["trucalc.vendor.deliverable"].sudo().search([
            ("order_id", "=", order.id), ("artifact_type", "=", "valuation"),
            ("is_current", "=", True),
        ])
        self.assertEqual(current, third)
        self.assertEqual(len(order.lifecycle_event_ids.filtered(
            lambda event: event.event_type == "valuation_received"
        )), 1)
        self.assertEqual(len(order.lifecycle_event_ids.filtered(
            lambda event: event.event_type == "valuation_revision_submitted"
        )), 2)
        latest_event = order.lifecycle_event_ids.filtered(
            lambda event: event.revision_request_event_id == request_two
        )
        self.assertEqual(latest_event.new_valuation_id, third)
        for field_name, value in original.items():
            self.assertEqual(first[field_name], value)
        with self.assertRaisesRegex(AccessError, "open revision request"):
            self._submit(authorization, filename="Replay Version 3.pdf")

    def test_revised_submission_preserves_vendor_and_invoice_boundaries(self):
        order, authorization, _projection = self._engaged()
        first = self._submit(authorization)
        invoice = self._submit(authorization, "vendor_invoice", "Invoice.pdf")
        self._request_revision(order, first)
        with self.assertRaises(AccessError):
            self._submit(
                authorization, filename="Wrong Vendor.pdf", actor=self.other_vendor_user,
            )
        second = self._submit(authorization, filename="Authorized Revision.pdf")
        self.assertEqual((invoice.version, invoice.is_current, invoice.status), (
            0, False, "submitted",
        ))
        with self.assertRaises(AccessError):
            second.with_user(self.admin).write({"is_current": False})

    def test_authorization_fails_closed(self):
        order, authorization, _projection = self._engaged()
        with self.assertRaises(AccessError):
            self._submit(authorization, actor=self.other_vendor_user)
        order.with_user(self.admin).action_reopen_bidding()
        with self.assertRaises(AccessError):
            self._submit(authorization)

        awaiting_order, awaiting_authorization, _projection = self._engaged(accept=False)
        with self.assertRaises(AccessError):
            self._submit(awaiting_authorization)
        self.assertEqual(awaiting_order.status, "engaged")

    def test_late_valuation_is_allowed(self):
        yesterday = fields.Date.subtract(fields.Date.today(), days=1)
        order, authorization, _projection = self._engaged(delivery_date=yesterday)
        self.assertTrue(self._submit(authorization))
        self.assertEqual(order.status, "report_received")

    def test_vendor_invoice_is_independent_before_and_after_valuation(self):
        first_order, first_auth, _projection = self._engaged()
        invoice = self._submit(
            first_auth, "vendor_invoice", "Invoice.pdf",
        )
        self.assertEqual(first_order.status, "engaged")
        self.assertEqual((invoice.version, invoice.is_current), (0, False))
        self.assertFalse(first_order.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "valuation_received"
        ))
        valuation = self._submit(first_auth)
        self.assertEqual(first_order.status, "report_received")
        self.assertTrue(valuation)

        second_order, second_auth, _projection = self._engaged()
        self._submit(second_auth)
        later_invoice = self._submit(
            second_auth, "vendor_invoice", "Later Invoice.PDF",
        )
        self.assertEqual(second_order.status, "report_received")
        self.assertTrue(later_invoice)
        with self.assertRaises(ValidationError):
            self._submit(second_auth, "vendor_invoice", "Replacement.pdf")

    def test_internal_and_external_access_boundaries(self):
        order, authorization, _projection = self._engaged()
        valuation = self._submit(authorization)
        invoice = self._submit(
            authorization, "vendor_invoice", "Invoice.pdf",
        )
        for internal in (
            self.admin, self.ops, self.admin_reviewer,
            self.operations_reviewer,
        ):
            self.assertTrue(valuation.with_user(internal).has_access("read"))
            self.assertTrue(invoice.with_user(internal).has_access("read"))
            self.assertEqual(
                valuation.with_user(internal)._authorize_download(internal), valuation,
            )
        for external in (self.bank, self.vendor_user, self.other_vendor_user):
            self.assertFalse(valuation.with_user(external).has_access("read"))
            self.assertFalse(invoice.with_user(external).has_access("read"))
        self.assertEqual(
            valuation.with_user(self.vendor_user)._authorize_download(self.vendor_user),
            valuation,
        )
        with self.assertRaises(AccessError):
            valuation.with_user(self.other_vendor_user)._authorize_download(
                self.other_vendor_user
            )
        with self.assertRaises(AccessError):
            invoice.with_user(self.bank)._authorize_download(self.bank)

        order.with_user(self.admin)._controlled_lifecycle_write({"reviewer_user_id": self.reviewer.id})
        order.with_user(self.admin).action_assign_reviewer()
        self.assertTrue(valuation.with_user(self.reviewer).has_access("read"))
        self.assertFalse(invoice.with_user(self.reviewer).has_access("read"))
        self.assertFalse(valuation.with_user(self.other_reviewer).has_access("read"))
        self.assertEqual(
            valuation.with_user(self.reviewer)._authorize_download(self.reviewer),
            valuation,
        )
        with self.assertRaises(AccessError):
            invoice.with_user(self.reviewer)._authorize_download(self.reviewer)
        order.with_user(self.reviewer).action_start_review()
        self.assertEqual(order.status, "under_review")

        self.operations_reviewer.sudo().write({
            "group_ids": [Command.unlink(self.env.ref(
                "trucalc_orders.group_trucalc_reviewer"
            ).id)],
        })
        self.assertTrue(
            valuation.with_user(self.operations_reviewer).has_access("read")
        )
        self.assertTrue(
            invoice.with_user(self.operations_reviewer).has_access("read")
        )

    def test_backend_and_portal_contracts_keep_deliverables_first_class(self):
        order_view = etree.fromstring(self.env.ref(
            "trucalc_orders.view_trucalc_order_form"
        ).arch.encode())
        self.assertFalse(order_view.xpath(
            ".//field[@name='document_ids']//field[@name='current_valuation_id']"
        ))
        self.assertFalse(order_view.xpath(
            ".//field[@name='valuation_filename_link']/ancestor::list"
        ))
        self.assertFalse(order_view.xpath(".//field[@name='version']"))
        self.assertTrue(order_view.xpath(".//field[@name='valuation_filename_link']"))
        self.assertTrue(order_view.xpath(".//field[@name='vendor_invoice_filename_link']"))
        order_information = order_view.xpath(
            ".//group[@string='Workflow Information']"
        )
        self.assertEqual(len(order_information), 1)
        self.assertEqual(
            [field.get("name") for field in order_information[0].xpath("./field")],
            [
                "assigned_vendor_id", "currency_id", "vendor_fee",
                "vendor_delivery_date", "vendor_engaged_at",
                "current_engagement_id", "engagement_action_required",
                "engagement_response_state", "engagement_requested_delivery_date",
                "engagement_request_reason", "engagement_decline_reason",
                "fee_override", "reviewer_user_id", "decline_reason",
            ],
        )
        self.assertFalse(order_information[0].xpath(
            ".//field[contains(@name, 'valuation') or contains(@name, 'invoice')]"
        ))
        deliverables_display = order_view.xpath(
            ".//div[@name='deliverables_display']"
        )
        self.assertEqual(len(deliverables_display), 1)
        self.assertEqual(
            deliverables_display[0].getparent(),
            order_information[0].getparent(),
        )
        for artifact in ("valuation", "vendor_invoice"):
            artifact_block = order_view.xpath(
                ".//div[@name='%s_display']" % artifact
            )
            self.assertEqual(len(artifact_block), 1)
            artifact_block = artifact_block[0]
            self.assertFalse(artifact_block.xpath(
                ".//*[contains(concat(' ', normalize-space(@class), ' '), ' o_row ')]"
            ))
            filename_line = artifact_block.xpath(
                "./div[.//field[@name='%s_filename_link']]" % artifact
            )
            self.assertEqual(len(filename_line), 1)
            metadata_line = artifact_block.xpath(
                "./div[.//field[@name='%s_submitted_at'] and "
                ".//field[@name='%s_deliverable_status']]" % (artifact, artifact)
            )
            self.assertEqual(len(metadata_line), 1)
            submitted_date = metadata_line[0].xpath(
                ".//field[@name='%s_submitted_at']" % artifact
            )
            self.assertEqual(len(submitted_date), 1)
            self.assertNotIn("widget", submitted_date[0].attrib)
            self.assertEqual(
                submitted_date[0].get("options"),
                "{'show_time': False, 'numeric': True}",
            )
            self.assertTrue(metadata_line[0].xpath(
                ".//field[@name='%s_deliverable_status']" % artifact
            ))
        portal = self.env.ref(
            "trucalc_orders.portal_my_trucalc_order"
        ).arch_db
        self.assertIn("deliverables/valuation", portal)
        self.assertIn("deliverables/vendor-invoice", portal)
        self.assertNotIn("Submitted by Vendor", portal)

    @unittest.skip(
        "Odoo's in-process test transaction cannot release the Order row lock to "
        "a competing committed worker; exercise in the external two-process harness."
    )
    def test_simultaneous_initial_valuation_submission_serializes(self):
        """External harness target; row lock and unique indexes provide the invariant."""


@tagged("post_install", "-at_install", "trucalc_vendor_deliverable_portal")
class TestVendorDeliverablePortal(HttpCase):
    password = "4D2-portal-test"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls._user("4d2-http-admin", "group_trucalc_admin")
        cls.reviewer = cls._user(
            "4d3-http-reviewer",
            ["group_trucalc_operations", "group_trucalc_reviewer"],
        )
        cls.bank = cls._user(
            "4d2-http-bank", "group_bank_admin", bank=cls.env.company,
        )
        cls.vendor = cls.env["trucalc.vendor"].create({"name": "4D2 HTTP Vendor"})
        cls.other_vendor = cls.env["trucalc.vendor"].create({
            "name": "4D2 HTTP Other Vendor",
        })
        cls.env["trucalc.vendor.fee"].create([
            {"vendor_id": cls.vendor.id, "service_type": "evaluation", "fee": 500},
            {"vendor_id": cls.other_vendor.id, "service_type": "evaluation", "fee": 600},
        ])
        cls.vendor_user = cls._user(
            "4d2-http-vendor", "group_vendor_portal", vendor=cls.vendor,
        )
        cls.other_vendor_user = cls._user(
            "4d2-http-other", "group_vendor_portal", vendor=cls.other_vendor,
        )

    @classmethod
    def _user(cls, login, group, vendor=False, bank=False):
        groups = group if isinstance(group, (list, tuple)) else [group]
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login, "login": login, "email": f"{login}@example.test",
            "password": cls.password,
            "group_ids": [Command.set([
                cls.env.ref(f"trucalc_orders.{group_name}").id
                for group_name in groups
            ])],
            "trucalc_vendor_id": vendor.id if vendor else False,
            "trucalc_bank_company_id": bank.id if bank else False,
        })

    def _engaged(self):
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "4D2 HTTP Borrower",
            "property_address": "42 Portal Upload Way",
            "company_id": self.env.company.id,
            "service_type": "evaluation",
            "due_date": fields.Date.add(fields.Date.today(), days=14),
        })
        order.with_user(self.admin).action_accept_request()
        order.with_user(self.admin).action_request_vendor_bids(
            self.vendor, fields.Datetime.add(fields.Datetime.now(), days=2),
        )
        bid = order.invitation_ids.with_user(
            self.vendor_user
        ).action_vendor_submit_response("standard_terms_accepted")
        bid.with_user(self.admin)._action_confirm_engagement()
        self.env.flush_all()
        self.env["trucalc.vendor.order"].invalidate_model()
        projection = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user
        ).search([("order_number", "=", order.order_number)])
        projection.action_vendor_accept_engagement()
        return order

    def _login(self, user):
        self.authenticate(user.login, self.password)

    def _csrf_token(self):
        return http.Request.csrf_token(self)

    def _upload(self, user, order, artifact, filename, content):
        self._login(user)
        field_name = (
            "valuation_file" if artifact == "valuation" else "vendor_invoice_file"
        )
        return self.url_open(
            f"/my/trucalc/orders/{order.order_number}/deliverables/{artifact}",
            data={"csrf_token": self._csrf_token()},
            files={field_name: (filename, content, "application/octet-stream")},
        )

    def test_portal_first_valuation_upload_locks_and_downloads(self):
        order = self._engaged()
        self._login(self.vendor_user)
        detail = self.url_open(
            f"/my/trucalc/orders/{order.order_number}?filterby=active"
        ).text
        self.assertIn("Valuation and Invoice", detail)
        self.assertIn("Valuation Upload:", detail)
        self.assertIn('for="valuation_file"', detail)
        self.assertIn('id="valuation_file"', detail)
        self.assertIn('for="vendor_invoice_file"', detail)
        self.assertIn('id="vendor_invoice_file"', detail)
        self.assertIn("Documents", detail)
        self.assertIn("No supporting documents are currently available.", detail)
        response = self._upload(
            self.vendor_user, order, "valuation", "Portal Valuation.pdf",
            b"%PDF-1.7\nportal valuation\n%%EOF",
        )
        self.assertEqual(response.status_code, 200)
        order.invalidate_recordset(["status"])
        self.assertEqual(order.status, "report_received")
        deliverable = self.env["trucalc.vendor.deliverable"].sudo().search([
            ("order_id", "=", order.id), ("artifact_type", "=", "valuation"),
        ])
        detail = self.url_open(f"/my/trucalc/orders/{order.order_number}").text
        self.assertIn("Portal Valuation.pdf", detail)
        self.assertNotIn("Submit Valuation</button>", detail)
        download = self.url_open(
            f"/my/trucalc/orders/{order.order_number}/deliverables/"
            f"{deliverable.id}/download"
        )
        self.assertEqual(download.content, b"%PDF-1.7\nportal valuation\n%%EOF")

    def test_portal_invoice_after_receipt_is_independent(self):
        order = self._engaged()
        self._upload(
            self.vendor_user, order, "valuation", "Valuation.pdf",
            b"%PDF-1.7\nvaluation",
        )
        response = self._upload(
            self.vendor_user, order, "vendor-invoice", "Invoice.pdf",
            b"%PDF-1.7\ninvoice",
        )
        self.assertEqual(response.status_code, 200)
        order.invalidate_recordset(["status"])
        self.assertEqual(order.status, "report_received")
        self.assertEqual(self.env["trucalc.order.lifecycle.event"].sudo().search_count([
            ("order_id", "=", order.id), ("event_type", "=", "valuation_received"),
        ]), 1)

    def test_portal_revision_instructions_and_single_revised_upload(self):
        order = self._engaged()
        self._upload(
            self.vendor_user, order, "valuation", "Initial.pdf",
            b"%PDF-1.7\ninitial",
        )
        valuation = self.env["trucalc.vendor.deliverable"].sudo().search([
            ("order_id", "=", order.id), ("artifact_type", "=", "valuation"),
        ])
        self.env["trucalc.order.lifecycle.event"].with_user(
            self.admin
        )._request_valuation_revision(
            order, valuation, "Replace the certification page.", self.admin,
        )
        self._login(self.vendor_user)
        detail = self.url_open(
            f"/my/trucalc/orders/{order.order_number}?filterby=submitted"
        ).text
        self.assertIn("Revision Requested", detail)
        self.assertIn("Replace the certification page.", detail)
        self.assertIn("earlier submission remains preserved", detail)
        self.assertIn("Submit Revised Valuation", detail)
        response = self._upload(
            self.vendor_user, order, "valuation", "Revised.pdf",
            b"%PDF-1.7\nrevised",
        )
        self.assertEqual(response.status_code, 200)
        detail = self.url_open(f"/my/trucalc/orders/{order.order_number}").text
        self.assertIn("Revised.pdf", detail)
        self.assertNotIn("Submit Revised Valuation", detail)
        valuations = self.env["trucalc.vendor.deliverable"].sudo().search([
            ("order_id", "=", order.id), ("artifact_type", "=", "valuation"),
        ], order="version")
        self.assertEqual(valuations.mapped("version"), [1, 2])
        self.assertEqual(valuations.filtered("is_current").filename, "Revised.pdf")
        revision_events = self.env["trucalc.order.lifecycle.event"].sudo().search([
            ("order_id", "=", order.id),
            ("event_type", "in", (
                "valuation_revision_requested", "valuation_revision_submitted",
            )),
        ])
        self.assertFalse(revision_events.with_user(self.bank).has_access("read"))
        self._login(self.bank)
        for valuation in valuations:
            attachment = self.env["ir.attachment"].sudo().search([
                ("res_model", "=", "trucalc.vendor.deliverable"),
                ("res_id", "=", valuation.id),
                ("res_field", "=", "file_data"),
            ], limit=1)
            self.assertTrue(attachment)
            self.assertEqual(
                self.url_open(f"/web/content/{attachment.id}").status_code, 404,
            )

    def test_cross_vendor_guessed_id_and_bank_surface_fail_closed(self):
        order = self._engaged()
        self._upload(
            self.vendor_user, order, "valuation", "Private Valuation.pdf",
            b"%PDF-1.7\nprivate",
        )
        deliverable = self.env["trucalc.vendor.deliverable"].sudo().search([
            ("order_id", "=", order.id),
        ])
        self._login(self.other_vendor_user)
        self.assertEqual(self.url_open(
            f"/my/trucalc/orders/{order.order_number}/deliverables/"
            f"{deliverable.id}/download"
        ).status_code, 404)
        self._login(self.bank)
        bank_page = self.url_open("/my/trucalc/bank/orders").text
        self.assertNotIn("Private Valuation.pdf", bank_page)
        self.assertNotIn("/deliverables/", bank_page)
        self.assertEqual(self.url_open(
            f"/trucalc/deliverables/{deliverable.id}/download"
        ).status_code, 404)

    def _bank_release_fixture(self):
        order = self._engaged()
        self._upload(
            self.vendor_user, order, "valuation", "Approved Valuation.pdf",
            b"%PDF-1.7\napproved",
        )
        valuation = self.env["trucalc.vendor.deliverable"].sudo().search([
            ("order_id", "=", order.id), ("artifact_type", "=", "valuation"),
        ])
        order.with_user(self.admin).action_assign_reviewer(self.reviewer)
        order.with_user(self.reviewer).action_start_review()
        return order, valuation

    def _bank_valuation_url(self, order, valuation):
        return (
            f"/my/trucalc/bank/orders/{order.order_number}/valuation/"
            f"{valuation.id}/download"
        )

    def _assert_bank_valuation_hidden(self, order, valuation):
        self.assertEqual(self.url_open(
            self._bank_valuation_url(order, valuation)
        ).status_code, 404)
        page = self.url_open(
            f"/my/trucalc/bank/orders/{order.order_number}/documents"
        ).text
        self.assertNotIn("Approved Valuation", page)
        self.assertNotIn(valuation.filename, page)
        self.assertNotIn(self._bank_valuation_url(order, valuation), page)
        tree = etree.HTML(page)
        self.assertFalse(tree.xpath(
            "//*[@data-oe-model='trucalc.vendor.deliverable' or "
            "@data-oe-model='trucalc.order.lifecycle.event']"
        ))
        self.assertNotIn("<strong>Submitted:</strong>", page)
        self.assertNotIn("<strong>Approved:</strong>", page)

    def _assert_bank_generic_download_denied(self, deliverable):
        attachment = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", deliverable._name),
            ("res_id", "=", deliverable.id), ("res_field", "=", "file_data"),
        ])
        self.assertEqual(len(attachment), 1)
        for url in (
            f"/web/content/{attachment.id}",
            f"/web/content?model={deliverable._name}&id={deliverable.id}&field=file_data",
        ):
            self.assertEqual(self.url_open(url).status_code, 404)

    def test_bank_release_requires_completion_after_approval(self):
        order, valuation = self._bank_release_fixture()
        self._login(self.bank)
        self._assert_bank_valuation_hidden(order, valuation)
        order.with_user(self.reviewer).action_approve_valuation(valuation)
        self.assertEqual(order.status, "under_review")
        self._assert_bank_valuation_hidden(order, valuation)
        self._assert_bank_generic_download_denied(valuation)

    def test_bank_completed_release_gate_compatibility(self):
        """Disposable status fixture tests release eligibility, not closeout."""
        order, first = self._bank_release_fixture()
        order.with_user(self.reviewer).action_request_valuation_revision(
            first, "Correct the certification page.",
        )
        self._upload(self.vendor_user, order, "valuation", "Current Valuation.pdf",
                     b"%PDF-1.7\napproved")
        valuation = self.env["trucalc.vendor.deliverable"].sudo().search([
            ("order_id", "=", order.id), ("artifact_type", "=", "valuation"),
            ("is_current", "=", True),
        ])
        # Reuse the trusted submission setup from the deliverable model tests;
        # this fixture tests Bank isolation, not the Vendor HTTP upload route.
        # The initial deliverable already holds the validated assignment context.
        authorization = first.authorization_id
        self.assertEqual(len(authorization), 1)
        invoice = self.env["trucalc.vendor.deliverable"].with_user(
            self.vendor_user
        )._submit(
            authorization, self.vendor_user, "vendor_invoice", "Private Invoice.pdf", PDF,
        )
        self.assertEqual(len(invoice), 1)
        order.with_user(self.reviewer).action_approve_valuation(valuation)
        # An approved prior version is unreachable through the preserved workflow.
        # Seed that historical event only in this isolated adversarial fixture.
        self.env["trucalc.order.lifecycle.event"]._log_valuation_approval(
            order, first, self.reviewer,
        )
        self._login(self.bank)
        self._assert_bank_valuation_hidden(order, valuation)
        for item in (first, invoice):
            self._assert_bank_valuation_hidden(order, item)
            self._assert_bank_generic_download_denied(item)

        order.with_user(self.admin)._controlled_lifecycle_write({"status": "completed"})
        page = self.url_open(
            f"/my/trucalc/bank/orders/{order.order_number}/documents"
        ).text
        self.assertIn("Approved Valuation", page)
        self.assertIn(valuation.filename, page)
        self.assertIn(self._bank_valuation_url(order, valuation), page)
        self.assertNotIn(first.filename, page)
        self.assertNotIn(invoice.filename, page)
        response = self.url_open(self._bank_valuation_url(order, valuation))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"%PDF-1.7\napproved")
        for item in (first, invoice):
            self.assertEqual(self.url_open(
                self._bank_valuation_url(order, item)
            ).status_code, 404)
        self._assert_bank_generic_download_denied(valuation)
        self.assertEqual(self.url_open(
            f"/my/trucalc/bank/orders/{order.order_number}/valuation/0/download"
        ).status_code, 404)
        other_company = self.env["res.company"].create({"name": "4D3A Other Bank"})
        other_bank = self._user("4d3a-other-bank", "group_bank_admin", bank=other_company)
        self._login(other_bank)
        self.assertEqual(self.url_open(
            self._bank_valuation_url(order, valuation)
        ).status_code, 404)
        self.assertEqual(self.url_open(
            f"/my/trucalc/bank/orders/{order.order_number}/documents"
        ).status_code, 404)

    def test_bank_completed_without_approval_is_denied(self):
        """Test-only completed fixture does not implement a completion action."""
        order, valuation = self._bank_release_fixture()
        order.with_user(self.admin)._controlled_lifecycle_write({"status": "completed"})
        self._login(self.bank)
        self._assert_bank_valuation_hidden(order, valuation)
        self._assert_bank_generic_download_denied(valuation)
