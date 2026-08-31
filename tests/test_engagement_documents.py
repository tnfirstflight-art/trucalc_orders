import base64
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_engagement_documents")
class TestEngagementDocuments(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls._user("4c2-admin", "group_trucalc_admin")
        cls.ops = cls._user("4c2-ops", "group_trucalc_operations")
        cls.reviewer = cls._user(
            "4c2-reviewer",
            ["group_trucalc_operations", "group_trucalc_reviewer"],
        )
        cls.operations_reviewer = cls._user(
            "4c2-operations-reviewer",
            ["group_trucalc_operations", "group_trucalc_reviewer"],
        )
        cls.admin_reviewer = cls._user(
            "4c2-admin-reviewer",
            ["group_trucalc_admin", "group_trucalc_reviewer"],
        )
        cls.bank_a = cls.env["res.company"].create({"name": "4C2 Bank A"})
        cls.bank_b = cls.env["res.company"].create({"name": "4C2 Bank B"})
        internal_companies = cls.env.company | cls.bank_a | cls.bank_b
        for internal_user in (
            cls.admin, cls.ops, cls.operations_reviewer, cls.admin_reviewer,
        ):
            internal_user.write({"company_ids": [Command.set(internal_companies.ids)]})
        cls.bank_admin = cls._user("4c2-bank-admin", "group_bank_admin", bank=cls.bank_a)
        cls.bank_requestor = cls._user("4c2-bank-requestor", "group_bank_requestor", bank=cls.bank_a)
        cls.bank_viewer = cls._user("4c2-bank-viewer", "group_bank_view_only", bank=cls.bank_a)
        cls.vendor = cls.env["trucalc.vendor"].create({"name": "4C2 Vendor"})
        cls.other_vendor = cls.env["trucalc.vendor"].create({"name": "4C2 Other Vendor"})
        cls.env["trucalc.vendor.fee"].create([
            {"vendor_id": cls.vendor.id, "service_type": "evaluation", "fee": 500},
            {"vendor_id": cls.other_vendor.id, "service_type": "evaluation", "fee": 600},
        ])
        cls.vendor_user = cls._user("4c2-vendor", "group_vendor_portal", vendor=cls.vendor)
        cls.other_vendor_user = cls._user("4c2-other-vendor", "group_vendor_portal", vendor=cls.other_vendor)
        cls.tag = cls.env["trucalc.document.tag"].with_user(cls.admin).create({"name": "4C2 Lease"})
        cls.other_tag = cls.env["trucalc.document.tag"].with_user(cls.admin).create({"name": "4C2 Legal"})

    @classmethod
    def _user(cls, login, group, bank=False, vendor=False):
        groups = group if isinstance(group, (list, tuple)) else [group]
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login, "login": login, "email": f"{login}@example.test",
            "group_ids": [Command.set([
                cls.env.ref(f"trucalc_orders.{group_name}").id
                for group_name in groups
            ])],
            "trucalc_bank_company_id": bank.id if bank else False,
            "trucalc_vendor_id": vendor.id if vendor else False,
        })

    def _order(self, company=None):
        return self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "4C2 Borrower", "property_address": "42 Document Way",
            "company_id": (company or self.bank_a).id, "service_type": "evaluation",
            "due_date": fields.Date.add(fields.Date.today(), days=14),
        })

    def _internal_document(self, order, filename="Lease.pdf", **extra):
        return self.env["trucalc.document"].with_user(self.admin).create({
            "order_id": order.id, "tag_id": self.tag.id, "filename": filename,
            "attachment": base64.b64encode(b"document"), **extra,
        })

    def _bank_document(self, order, actor=None, filename="Bank.pdf"):
        actor = actor or self.bank_requestor
        return self.env["trucalc.document"].with_user(actor)._create_bank_document(
            order, self.tag, filename, base64.b64encode(b"bank document"), actor,
        )

    def _modal_document(self, order, actor, filename):
        action = order.with_user(actor).action_add_document()
        document_model = self.env["trucalc.document"].with_user(actor).with_context(
            action["context"]
        )
        values = {
            # Mirrors Odoo's web create payload: force-saved readonly order_id is
            # included, while other readonly technical defaults are omitted.
            "order_id": action["context"]["default_order_id"],
            "tag_id": self.tag.id,
            "filename": filename,
            "attachment": base64.b64encode(b"modal document"),
            "visible_before_engagement": False,
            "visible_after_engagement": False,
        }
        return document_model.create(values)

    def _solicited(self, vendors=None):
        order = self._order()
        order.with_user(self.admin).action_accept_request()
        order.with_user(self.admin).action_request_vendor_bids(
            vendors or self.vendor, fields.Datetime.now() + timedelta(days=2),
        )
        return order

    def test_safe_defaults_filename_size_and_duplicates(self):
        order = self._order()
        document = self._internal_document(
            order, "Smith Property - Rent Roll (2026).xlsx",
            visible_before_engagement=True, visible_after_engagement=True,
        )
        self.assertFalse(document.visible_before_engagement)
        self.assertFalse(document.visible_after_engagement)
        self.assertEqual(document.origin, "trucalc")
        for filename in ("bad/name.pdf", "bad\\name.pdf", "bad#.pdf", "noextension"):
            with self.assertRaises(ValidationError):
                self._internal_document(order, filename)
        with self.assertRaises(ValidationError):
            self._internal_document(order, "smith property - rent roll (2026).XLSX")
        self.assertTrue(self._internal_document(self._order(), document.filename))
        with self.assertRaises(ValidationError):
            self.env["trucalc.document"]._validate_file_value(
                base64.b64encode(b"x" * (50 * 1024 * 1024 + 1))
            )
        self.assertEqual(
            self.env["trucalc.document"]._validate_file_value(
                base64.b64encode(b"x" * (50 * 1024 * 1024))
            ), 50 * 1024 * 1024,
        )

    def test_extension_allowlist_is_shared_and_final_suffix_based(self):
        order = self._order()
        approved = (
            "pdf", "doc", "docx", "xls", "xlsx",
            "jpg", "jpeg", "png", "tif", "tiff",
        )
        for extension in approved:
            document = self._internal_document(
                order, "Approved %s.%s" % (extension, extension)
            )
            self.assertEqual(document.origin, "trucalc")

        for filename in ("report.PDF", "photo.JPG", "valuation.XLSX", "scan.TiFf"):
            self.assertTrue(self._internal_document(order, filename))

        for filename in (
            "blocked.exe", "blocked.js", "blocked.zip", "blocked.docm",
            "blocked.xlsm", "blocked.pptx", "report.pdf.exe", "report.pdf.js",
        ):
            with self.assertRaisesRegex(ValidationError, "Permitted file types"):
                self._internal_document(order, filename)

        # Only the final suffix defines policy; this pass does not inspect content.
        self.assertTrue(self._internal_document(order, "report.exe.pdf"))
        with self.assertRaises(ValidationError):
            self._internal_document(order, "extensionless")

    def test_source_filename_validation_and_immutability(self):
        order = self._order()
        before = self.env["trucalc.document"].sudo().search_count([
            ("order_id", "=", order.id),
        ])
        event_before = self.env["trucalc.document.event"].sudo().search_count([
            ("order_id", "=", order.id),
        ])
        attachment_before = self.env["ir.attachment"].sudo().search_count([
            ("res_model", "=", "trucalc.document"),
        ])
        for source_filename in ("Test # Lease.pdf", "Extensionless"):
            with self.assertRaises(ValidationError):
                self._modal_document(order, self.admin, source_filename)
        action = order.with_user(self.admin).action_add_document()
        modal_model = self.env["trucalc.document"].with_user(self.admin).with_context(
            action["context"]
        )
        modal_defaults = modal_model.default_get([
            "order_id", "origin", "uploaded_by", "upload_date", "active",
            "visible_before_engagement", "visible_after_engagement",
        ])
        with self.assertRaises(ValidationError):
            modal_model.create({
                **modal_defaults,
                "filename": "Missing Tag.pdf",
                "attachment": base64.b64encode(b"document"),
            })
        with self.assertRaises(ValidationError):
            modal_model.create({
                **modal_defaults,
                "tag_id": self.tag.id,
                "filename": "Oversized.pdf",
                "attachment": base64.b64encode(b"x" * (50 * 1024 * 1024 + 1)),
            })
        self.assertEqual(self.env["trucalc.document"].sudo().search_count([
            ("order_id", "=", order.id),
        ]), before)
        self.assertEqual(self.env["trucalc.document.event"].sudo().search_count([
            ("order_id", "=", order.id),
        ]), event_before)
        self.assertEqual(self.env["ir.attachment"].sudo().search_count([
            ("res_model", "=", "trucalc.document"),
        ]), attachment_before)

        document = self._internal_document(order, "Test Lease.pdf")
        self.assertEqual(document.name, "Test Lease.pdf")
        self.assertEqual(document.filename, "Test Lease.pdf")
        with self.assertRaises(AccessError):
            document.with_user(self.admin).write({"filename": "Renamed.pdf"})
        with self.assertRaises(AccessError):
            document.with_user(self.admin).write({
                "attachment": base64.b64encode(b"replacement"),
            })

    def test_exact_ascii_invoice_filename_and_unicode_lookalikes(self):
        order = self._order()
        filename = "119952 - Inspection Invoice.pdf"
        self.assertEqual(repr(filename), "'119952 - Inspection Invoice.pdf'")
        self.assertEqual(len(filename), 31)
        self.assertEqual(
            [ord(character) for character in filename[6:9]],
            [0x20, 0x2D, 0x20],
        )
        document = self._modal_document(order, self.admin, filename)
        self.assertEqual(document.filename, filename)
        self.assertEqual(document.name, filename)

        for invalid_filename in (
            "Test # Lease.pdf",
            "Extensionless",
            "119952 – Inspection Invoice.pdf",
            "119952 — Inspection Invoice.pdf",
            "119952\N{NO-BREAK SPACE}- Inspection Invoice.pdf",
            "folder/119952 - Inspection Invoice.pdf",
            "folder\\119952 - Inspection Invoice.pdf",
        ):
            with self.assertRaises(ValidationError):
                self._modal_document(order, self.admin, invalid_filename)
        with self.assertRaises(ValidationError):
            self.env["trucalc.document"].with_user(self.admin).create({
                "order_id": self._order().id,
                "tag_id": self.tag.id,
                "name": filename,
                "filename": "Test # Lease.pdf",
                "attachment": base64.b64encode(b"authoritative filename"),
            })

    def test_add_document_action_is_independent_and_discard_is_non_mutating(self):
        order = self._order()
        document_count = self.env["trucalc.document"].sudo().search_count([
            ("order_id", "=", order.id),
        ])
        event_count = self.env["trucalc.document.event"].sudo().search_count([
            ("order_id", "=", order.id),
        ])
        attachment_count = self.env["ir.attachment"].sudo().search_count([
            ("res_model", "=", "trucalc.document"),
        ])
        action = order.with_user(self.admin).action_add_document()
        self.assertEqual(action["res_model"], "trucalc.document")
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["context"]["default_order_id"], order.id)
        self.assertEqual(action["context"]["default_origin"], "trucalc")
        self.assertEqual(action["context"]["trucalc_document_order_id"], order.id)
        defaults = self.env["trucalc.document"].with_user(self.admin).with_context(
            action["context"]
        ).default_get(["order_id", "origin", "uploaded_by", "upload_date", "active"])
        self.assertEqual(defaults["order_id"], order.id)
        self.assertEqual(defaults["origin"], "trucalc")
        self.assertEqual(self.env["trucalc.document"].sudo().search_count([
            ("order_id", "=", order.id),
        ]), document_count)
        self.assertEqual(self.env["trucalc.document.event"].sudo().search_count([
            ("order_id", "=", order.id),
        ]), event_count)
        self.assertEqual(self.env["ir.attachment"].sudo().search_count([
            ("res_model", "=", "trucalc.document"),
        ]), attachment_count)
        with self.assertRaises(AccessError):
            order.with_user(self.reviewer).action_add_document()

        order_view = self.env.ref("trucalc_orders.view_trucalc_order_form").arch_db
        document_view = self.env.ref("trucalc_orders.view_trucalc_document_form").arch_db
        self.assertIn(
            '<list create="false" delete="false" editable="bottom"',
            order_view,
        )
        self.assertIn('open_form_view="false" limit="10000"', order_view)
        self.assertIn('widget="many2one_autosave"', order_view)
        self.assertEqual(order_view.count('widget="boolean_toggle"'), 2)
        self.assertEqual(order_view.count("options=\"{'autosave': True}\""), 2)
        self.assertIn('confirm-title="Delete Document?"', order_view)
        self.assertIn('confirm-label="Delete"', order_view)
        self.assertIn("This action cannot be undone.", order_view)
        self.assertNotIn('<form delete="false">', order_view)
        self.assertIn(
            '<field name="filename" readonly="1" force_save="1"/>', document_view
        )
        self.assertIn(
            '<field name="order_id" readonly="1" force_save="1"/>', document_view
        )

    def test_modal_contract_valid_admin_ops_and_fixed_order(self):
        order = self._order()
        before_documents = self.env["trucalc.document"].sudo().search_count([
            ("order_id", "=", order.id),
        ])
        before_events = self.env["trucalc.document.event"].sudo().search_count([
            ("order_id", "=", order.id),
        ])
        admin_document = self._modal_document(order, self.admin, "Admin Modal.pdf")
        ops_document = self._modal_document(order, self.ops, "Ops Modal.pdf")
        self.assertEqual(self.env["trucalc.document"].sudo().search_count([
            ("order_id", "=", order.id),
        ]), before_documents + 2)
        self.assertEqual(self.env["trucalc.document.event"].sudo().search_count([
            ("order_id", "=", order.id),
        ]), before_events + 2)
        for document, actor in (
            (admin_document, self.admin), (ops_document, self.ops),
        ):
            self.assertEqual(document.order_id, order)
            self.assertEqual(document.company_id, order.company_id)
            self.assertEqual(document.origin, "trucalc")
            self.assertEqual(document.uploaded_by, actor)
            self.assertFalse(document.visible_before_engagement)
            self.assertFalse(document.visible_after_engagement)
            self.assertEqual(len(document.event_ids), 1)
            self.assertEqual(document.event_ids.event_type, "uploaded")

        other_order = self._order()
        action = order.with_user(self.admin).action_add_document()
        document_count = self.env["trucalc.document"].sudo().search_count([])
        event_count = self.env["trucalc.document.event"].sudo().search_count([])
        attachment_count = self.env["ir.attachment"].sudo().search_count([
            ("res_model", "=", "trucalc.document"),
        ])
        with self.assertRaises(AccessError):
            self.env["trucalc.document"].with_user(self.admin).with_context(
                action["context"]
            ).create({
                "order_id": other_order.id,
                "tag_id": self.tag.id,
                "filename": "Cross Order.pdf",
                "attachment": base64.b64encode(b"cross order"),
            })
        foreign_company = self.env["res.company"].create({"name": "4C2 Foreign Bank"})
        foreign_order = self.env["trucalc.order"].sudo().create({
            "borrower": "Foreign Borrower",
            "property_address": "99 Foreign Way",
            "company_id": foreign_company.id,
            "service_type": "evaluation",
            "due_date": fields.Date.add(fields.Date.today(), days=14),
        })
        with self.assertRaises(AccessError):
            self.env["trucalc.document"].with_user(self.admin).with_context(
                action["context"]
            ).create({
                "order_id": foreign_order.id,
                "tag_id": self.tag.id,
                "filename": "Cross Company.pdf",
                "attachment": base64.b64encode(b"cross company"),
            })
        self.assertEqual(
            self.env["trucalc.document"].sudo().search_count([]), document_count
        )
        self.assertEqual(
            self.env["trucalc.document.event"].sudo().search_count([]), event_count
        )
        self.assertEqual(self.env["ir.attachment"].sudo().search_count([
            ("res_model", "=", "trucalc.document"),
        ]), attachment_count)

    def test_internal_quick_download_authorization_and_no_audit(self):
        order = self._order()
        order.with_user(self.admin).write({"reviewer_user_id": self.reviewer.id})
        order.with_user(self.admin)._controlled_lifecycle_write({
            "status": "reviewer_assigned",
        })
        document = self._internal_document(order)
        unassigned_document = self._internal_document(
            self._order(self.bank_b), "Unassigned.pdf",
        )
        event_count = len(document.event_ids)
        for internal in (
            self.admin, self.ops, self.admin_reviewer,
            self.operations_reviewer,
        ):
            action = document.with_user(internal).action_download()
            self.assertEqual(action["type"], "ir.actions.act_url")
            self.assertIn(f"model=trucalc.document&id={document.id}", action["url"])
            self.assertTrue(unassigned_document.with_user(internal).has_access("read"))
        self.assertEqual(len(document.event_ids), event_count)
        self.assertFalse(document.with_user(self.reviewer).has_access("read"))
        with self.assertRaises(AccessError):
            document.with_user(self.reviewer).action_download()
        self.assertFalse(document.with_user(self.reviewer).has_access("write"))
        self.operations_reviewer.sudo().write({
            "group_ids": [Command.unlink(self.env.ref(
                "trucalc_orders.group_trucalc_reviewer"
            ).id)],
        })
        self.assertTrue(
            document.with_user(self.operations_reviewer).has_access("read")
        )
        self.assertTrue(
            unassigned_document.with_user(self.operations_reviewer).has_access("read")
        )
        for user in (self.bank_requestor, self.bank_viewer, self.vendor_user):
            with self.assertRaises(AccessError):
                document.with_user(user).action_download()
        document.with_user(self.ops).action_controlled_delete()
        with self.assertRaises(AccessError):
            document.with_context(active_test=False).with_user(self.admin).action_download()
        self.assertEqual(len(document.event_ids), event_count + 1)

    def test_roles_tags_immutability_and_controlled_delete(self):
        order = self._order()
        document = self._internal_document(order)
        self.assertEqual(document.event_ids.event_type, "uploaded")
        for user in (self.reviewer, self.bank_viewer, self.vendor_user):
            with self.assertRaises(AccessError):
                self.env["trucalc.document"].with_user(user).create({})
            with self.assertRaises(AccessError):
                document.with_user(user).write({"visible_before_engagement": True})
            with self.assertRaises(AccessError):
                document.with_user(user).write({"tag_id": self.other_tag.id})
        with self.assertRaises(AccessError):
            self.env["trucalc.document.tag"].with_user(self.ops).create({"name": "Denied"})

        event_count = len(document.event_ids)
        document.with_user(self.admin).write({"tag_id": self.other_tag.id})
        self.assertEqual(len(document.event_ids), event_count + 1)
        self.assertEqual(document.event_ids.sorted("id")[-1].event_type, "tag_changed")
        document.with_user(self.ops).write({"tag_id": self.tag.id})
        self.assertEqual(len(document.event_ids), event_count + 2)
        self.assertEqual(document.event_ids.sorted("id")[-1].event_type, "tag_changed")
        self.tag.active = False
        self.assertEqual(document.tag_id, self.tag)
        document.with_user(self.ops).write({"tag_id": self.other_tag.id})
        with self.assertRaises(ValidationError):
            document.with_user(self.admin).write({"tag_id": self.tag.id})
        self.tag.active = True

        visibility_event_count = len(document.event_ids)
        document.with_user(self.admin).write({"visible_before_engagement": True})
        document.with_user(self.ops).write({"visible_after_engagement": True})
        visibility_events = document.event_ids.filtered(
            lambda event: event.event_type == "visibility_changed"
        )
        self.assertEqual(len(document.event_ids), visibility_event_count + 2)
        self.assertEqual(len(visibility_events), 2)
        event_count = len(document.event_ids)
        document.with_user(self.admin).write({"visible_before_engagement": True})
        self.assertEqual(len(document.event_ids), event_count)
        attachment = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "trucalc.document"), ("res_id", "=", document.id),
            ("res_field", "=", "attachment"),
        ])
        delete_action = document.with_user(self.ops).action_controlled_delete()
        self.assertEqual(delete_action, {
            "type": "ir.actions.client",
            "tag": "reload",
        })
        self.assertFalse(document.active)
        self.assertFalse(document.attachment)
        self.assertFalse(attachment.exists())
        self.assertEqual(document.event_ids.sorted("id")[-1].event_type, "deleted")
        order.invalidate_recordset(["document_ids"])
        self.assertNotIn(document, order.document_ids)
        self.assertTrue(self._internal_document(order, "Lease.pdf"))
        with self.assertRaises(AccessError):
            document.with_user(self.admin).unlink()

    def test_bank_boundary_and_notification_readiness(self):
        order = self._order()
        document = self._bank_document(order)
        self.assertEqual((document.origin, document.originating_bank_id), ("bank", self.bank_a))
        self.assertFalse(document.event_ids.trucalc_notification_worthy)
        with self.assertRaises(AccessError):
            self._bank_document(self._order(self.bank_b), filename="Foreign.pdf")
        with self.assertRaises(AccessError):
            self._bank_document(order, self.bank_viewer, "Viewer.pdf")
        self.assertFalse(self.env["trucalc.document"].with_user(self.bank_requestor).has_access("read"))
        order.with_user(self.admin).action_accept_request()
        late = self._bank_document(order, filename="Late.pdf")
        self.assertTrue(late.event_ids.trucalc_notification_worthy)

    def test_vendor_before_after_decline_and_reopen(self):
        order = self._solicited(self.vendor | self.other_vendor)
        document = self._internal_document(order)
        invitation = order.invitation_ids.filtered(lambda item: item.vendor_id == self.vendor)
        authorization = self.env["trucalc.order.vendor.authorization"].sudo().search([
            ("invitation_id", "=", invitation.id),
        ])
        document.with_user(self.ops).write({"visible_before_engagement": True})
        self.assertIn(document, self.env["trucalc.document"]._vendor_authorized_documents(
            authorization, self.vendor_user
        ))
        self.assertFalse(self.env["trucalc.document"]._vendor_authorized_documents(
            authorization, self.other_vendor_user
        ))
        bid = invitation.with_user(self.vendor_user).action_vendor_submit_response(
            "standard_terms_accepted"
        )
        bid.with_user(self.admin)._action_confirm_engagement()
        assignment = order.vendor_authorization_ids.filtered("active")
        self.assertFalse(self.env["trucalc.document"]._vendor_authorized_documents(
            assignment, self.vendor_user
        ))
        self.env.flush_all()
        self.env["trucalc.vendor.order"].invalidate_model()
        projection = self.env["trucalc.vendor.order"].with_user(self.vendor_user).search([
            ("order_number", "=", order.order_number),
        ])
        projection.action_vendor_accept_engagement()
        document.with_user(self.ops).write({"visible_after_engagement": True})
        self.assertIn(document, self.env["trucalc.document"]._vendor_authorized_documents(
            assignment, self.vendor_user
        ))
        event = document.event_ids.filtered("vendor_notification_worthy")
        self.assertEqual(len(event), 1)
        self.assertEqual(event.vendor_id, self.vendor)
        order.with_user(self.admin).action_reopen_bidding()
        self.assertFalse(self.env["trucalc.document"]._vendor_authorized_documents(
            assignment, self.vendor_user
        ))

    def test_declined_engagement_fails_even_with_active_assignment(self):
        order = self._solicited()
        invitation = order.invitation_ids
        bid = invitation.with_user(self.vendor_user).action_vendor_submit_response(
            "standard_terms_accepted"
        )
        bid.with_user(self.admin)._action_confirm_engagement()
        assignment = order.vendor_authorization_ids.filtered("active")
        document = self._internal_document(order)
        document.with_user(self.admin).write({"visible_after_engagement": True})
        self.env.flush_all()
        self.env["trucalc.vendor.order"].invalidate_model()
        projection = self.env["trucalc.vendor.order"].with_user(self.vendor_user).search([
            ("order_number", "=", order.order_number),
        ])
        projection.action_vendor_decline_engagement("Cannot perform")
        self.assertTrue(assignment.active)
        self.assertFalse(self.env["trucalc.document"]._vendor_authorized_documents(
            assignment, self.vendor_user
        ))

    def test_audit_crud_is_denied(self):
        document = self._internal_document(self._order())
        event = document.event_ids
        with self.assertRaises(AccessError):
            event.with_user(self.admin).write({"filename": "Forged.pdf"})
        with self.assertRaises(AccessError):
            event.with_user(self.admin).unlink()
