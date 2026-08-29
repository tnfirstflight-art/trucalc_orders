import base64

from lxml import etree

from odoo import Command, fields, http
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install", "trucalc_bank_order_portal")
class TestBankOrderPortal(HttpCase):
    password = "4C2-bank-portal-test"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls._user("admin", "group_trucalc_admin")
        cls.bank_a = cls.env["res.company"].create({"name": "4C2 Portal Bank A"})
        cls.bank_b = cls.env["res.company"].create({"name": "4C2 Portal Bank B"})
        cls.admin.write({
            "company_ids": [Command.set((cls.env.company | cls.bank_a | cls.bank_b).ids)]
        })
        cls.bank_admin = cls._user("bank-admin", "group_bank_admin", cls.bank_a)
        cls.bank_requestor = cls._user(
            "bank-requestor", "group_bank_requestor", cls.bank_a
        )
        cls.bank_viewer = cls._user(
            "bank-viewer", "group_bank_view_only", cls.bank_a
        )
        cls.other_bank_user = cls._user(
            "other-bank", "group_bank_admin", cls.bank_b
        )
        cls.plain_portal = cls.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "4C2 Plain Portal",
            "login": "4c2-plain-portal",
            "password": cls.password,
            "group_ids": [Command.set([cls.env.ref("base.group_portal").id])],
        })
        cls.legacy_internal_bank_user = cls.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "4C2 Legacy Internal Bank User",
            "login": "4c2-legacy-internal-bank",
            "password": cls.password,
            "company_id": cls.bank_a.id,
            "company_ids": [Command.set([cls.bank_a.id])],
            "group_ids": [Command.set([cls.env.ref("base.group_user").id])],
        })
        cls.order_a = cls._order(cls.bank_a, "Approved Bank Borrower", "101 Bank A Way")
        cls.order_b = cls._order(cls.bank_b, "Foreign Bank Borrower", "202 Bank B Way")
        cls.tag = cls.env["trucalc.document.tag"].with_user(cls.admin).create({
            "name": "4C2 Bank Portal Supporting",
        })
        cls.bank_document = cls.env["trucalc.document"].with_user(
            cls.bank_requestor
        )._create_bank_document(
            cls.order_a, cls.tag, "Bank Supporting.pdf",
            base64.b64encode(b"bank supporting"), cls.bank_requestor,
        )
        cls.internal_document = cls.env["trucalc.document"].with_user(cls.admin).create({
            "order_id": cls.order_a.id,
            "tag_id": cls.tag.id,
            "filename": "Internal Only.pdf",
            "attachment": base64.b64encode(b"internal"),
        })

    @classmethod
    def _user(cls, suffix, group, bank=False):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "4C2 Portal %s" % suffix,
            "login": "4c2-portal-%s" % suffix,
            "password": cls.password,
            "group_ids": [Command.set([cls.env.ref("trucalc_orders.%s" % group).id])],
            "trucalc_bank_company_id": bank.id if bank else False,
        })

    @classmethod
    def _order(cls, bank, borrower, address):
        return cls.env["trucalc.order"].with_user(cls.admin).create({
            "borrower": borrower,
            "property_address": address,
            "company_id": bank.id,
            "service_type": "evaluation",
            "due_date": fields.Date.add(fields.Date.today(), days=14),
        })

    def _login(self, user):
        self.authenticate(user.login, self.password)

    def _csrf_token(self):
        return http.Request.csrf_token(self)

    def _document_artifact_counts(self, order):
        documents = self.env["trucalc.document"].sudo().with_context(
            active_test=False
        ).search([("order_id", "=", order.id)])
        return (
            len(documents),
            self.env["trucalc.document.event"].sudo().search_count([
                ("order_id", "=", order.id),
            ]),
            self.env["ir.attachment"].sudo().search_count([
                ("res_model", "=", "trucalc.document"),
                ("res_id", "in", documents.ids),
                ("res_field", "=", "attachment"),
            ]),
        )

    def _portal_upload(self, user, order, tag_id, filename, content):
        self._login(user)
        return self.url_open(
            "/my/trucalc/bank/orders/%s/documents/upload" % order.order_number,
            data={"csrf_token": self._csrf_token(), "tag_id": tag_id},
            files={"document_file": (filename, content, "application/octet-stream")},
        )

    def test_bank_home_list_detail_and_cross_bank_idor(self):
        for user in (
            self.bank_admin, self.bank_requestor, self.bank_viewer,
        ):
            self._login(user)
            self.assertIn("My TruCalc Requests", self.url_open("/my").text)
            listing = self.url_open("/my/trucalc/bank/orders").text
            self.assertIn(self.order_a.order_number, listing)
            self.assertIn("Approved Bank Borrower", listing)
            self.assertIn("101 Bank A Way", listing)
            self.assertNotIn(self.order_b.order_number, listing)
            self.assertNotIn("Foreign Bank Borrower", listing)
            detail = self.url_open(
                "/my/trucalc/bank/orders/%s/documents" % self.order_a.order_number
            ).text
            self.assertIn("Approved Bank Borrower", detail)
            self.assertIn("101 Bank A Way", detail)
            self.assertEqual(
                self.url_open(
                    "/my/trucalc/bank/orders/%s/documents" % self.order_b.order_number
                ).status_code,
                404,
            )

    def test_surface_disclosure_and_role_controls(self):
        forbidden = (
            "Assigned Vendor", "Vendor Fee", "Review Fee", "TruCalc Fee",
            "Bidding", "Engagement", "Reviewer", "Internal Notes",
            "Vendor Delivery",
        )
        for user, can_upload in (
            (self.bank_admin, True),
            (self.bank_requestor, True),
            (self.bank_viewer, False),
        ):
            self._login(user)
            detail = self.url_open(
                "/my/trucalc/bank/orders/%s/documents" % self.order_a.order_number
            ).text
            labels = {
                " ".join(node.itertext()).strip()
                for node in etree.HTML(detail).xpath("//dt")
            }
            self.assertEqual(labels, {
                "Order Number", "Borrower", "Property Address", "Service Type",
                "Client Due Date", "Status",
            })
            self.assertEqual('name="document_file"' in detail, can_upload)
            for value in forbidden:
                self.assertNotIn(value, detail)

    def test_document_filtering_and_controlled_download(self):
        self._login(self.bank_viewer)
        detail = self.url_open(
            "/my/trucalc/bank/orders/%s/documents" % self.order_a.order_number
        ).text
        self.assertIn("Bank Supporting.pdf", detail)
        self.assertNotIn("Internal Only.pdf", detail)
        allowed = self.url_open(
            "/my/trucalc/bank/orders/%s/documents/%s/download"
            % (self.order_a.order_number, self.bank_document.id)
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.content, b"bank supporting")
        self.assertEqual(
            self.url_open(
                "/my/trucalc/bank/orders/%s/documents/%s/download"
                % (self.order_a.order_number, self.internal_document.id)
            ).status_code,
            404,
        )

        attachment = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "trucalc.document"),
            ("res_id", "=", self.internal_document.id),
            ("res_field", "=", "attachment"),
        ], limit=1)
        self.assertTrue(attachment)
        self.assertEqual(self.url_open("/web/content/%s" % attachment.id).status_code, 404)

    def test_generic_order_attachment_bypass_is_blocked(self):
        self._login(self.bank_requestor)
        domain = [
            ("res_model", "=", "trucalc.order"),
            ("res_id", "=", self.order_a.id),
        ]
        before = self.env["ir.attachment"].sudo().search_count(domain)
        response = self.url_open(
            "/mail/attachment/upload",
            data={
                "csrf_token": self._csrf_token(),
                "thread_id": self.order_a.id,
                "thread_model": "trucalc.order",
            },
            files={"ufile": ("Bypass.pdf", b"bypass", "application/pdf")},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.env["ir.attachment"].sudo().search_count(domain), before)

        response = self.url_open(
            "/web/binary/upload_attachment",
            data={
                "csrf_token": self._csrf_token(),
                "model": "trucalc.order",
                "id": self.order_a.id,
            },
            files={"ufile": ("Bypass.pdf", b"bypass", "application/pdf")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("not allowed", response.text)
        self.assertEqual(self.env["ir.attachment"].sudo().search_count(domain), before)

        with self.assertRaises(AccessError):
            self.env["ir.attachment"].with_user(self.bank_requestor).create({
                "name": "Direct.pdf", "raw": b"direct",
                "res_model": "trucalc.order", "res_id": self.order_a.id,
            })
        controlled_attachment = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "trucalc.document"),
            ("res_id", "=", self.bank_document.id),
            ("res_field", "=", "attachment"),
        ], limit=1)
        with self.assertRaises(AccessError):
            controlled_attachment.with_user(self.bank_requestor).write({
                "res_model": "trucalc.order", "res_id": self.order_a.id,
            })
        self.assertEqual(self.env["ir.attachment"].sudo().search_count(domain), before)

    def test_internal_order_mail_attachment_remains_available(self):
        self._login(self.admin)
        domain = [
            ("res_model", "=", "trucalc.order"),
            ("res_id", "=", self.order_a.id),
            ("name", "=", "Internal Chatter.txt"),
        ]
        response = self.url_open(
            "/mail/attachment/upload",
            data={
                "csrf_token": self._csrf_token(),
                "thread_id": self.order_a.id,
                "thread_model": "trucalc.order",
            },
            files={"ufile": ("Internal Chatter.txt", b"internal", "text/plain")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.env["ir.attachment"].sudo().search_count(domain), 1)

    def test_controlled_bank_multipart_upload(self):
        before = self._document_artifact_counts(self.order_a)
        response = self._portal_upload(
            self.bank_requestor, self.order_a, self.tag.id,
            "Portal Upload.pdf", b"portal upload",
        )
        self.assertEqual(response.status_code, 200)
        document = self.env["trucalc.document"].sudo().search([
            ("order_id", "=", self.order_a.id),
            ("filename", "=", "Portal Upload.pdf"),
        ])
        self.assertEqual(len(document), 1)
        self.assertEqual(document.tag_id, self.tag)
        self.assertEqual(document.order_id, self.order_a)
        self.assertEqual(document.origin, "bank")
        self.assertEqual(document.originating_bank_id, self.bank_a)
        self.assertEqual(document.uploaded_by, self.bank_requestor)
        self.assertFalse(document.visible_before_engagement)
        self.assertFalse(document.visible_after_engagement)
        self.assertTrue(document.active)
        self.assertEqual(document.attachment, base64.b64encode(b"portal upload"))
        self.assertEqual(len(document.event_ids), 1)
        self.assertEqual(document.event_ids.event_type, "uploaded")
        after = self._document_artifact_counts(self.order_a)
        self.assertEqual(after, tuple(value + 1 for value in before))

    def test_controlled_bank_multipart_validation_failures_are_atomic(self):
        inactive_tag = self.env["trucalc.document.tag"].with_user(self.admin).create({
            "name": "4C2 Inactive Portal Tag", "active": False,
        })
        cases = (
            (self.bank_requestor, self.order_a, 0, "Missing Tag.pdf", b"file"),
            (self.bank_requestor, self.order_a, inactive_tag.id, "Inactive Tag.pdf", b"file"),
            (self.bank_requestor, self.order_a, 999999999, "Forged Tag.pdf", b"file"),
            (self.bank_requestor, self.order_a, self.tag.id, "Invalid#.pdf", b"file"),
            (self.bank_requestor, self.order_a, self.tag.id, "Extensionless", b"file"),
            (self.bank_requestor, self.order_a, self.tag.id, "Blocked.exe", b"file"),
            (self.bank_requestor, self.order_a, self.tag.id, "Empty.pdf", b""),
            (self.bank_requestor, self.order_a, self.tag.id, "Bank Supporting.pdf", b"duplicate"),
            (self.bank_requestor, self.order_b, self.tag.id, "Cross Bank.pdf", b"file"),
            (self.bank_viewer, self.order_a, self.tag.id, "Viewer.pdf", b"file"),
        )
        before_a = self._document_artifact_counts(self.order_a)
        before_b = self._document_artifact_counts(self.order_b)
        for user, order, tag_id, filename, content in cases:
            response = self._portal_upload(user, order, tag_id, filename, content)
            self.assertIn(response.status_code, (200, 404))
        self.assertEqual(self._document_artifact_counts(self.order_a), before_a)
        self.assertEqual(self._document_artifact_counts(self.order_b), before_b)

    def test_non_bank_personas_fail_closed(self):
        self.authenticate(None, None)
        self.assertIn(
            self.url_open("/my/trucalc/bank/orders", allow_redirects=False).status_code,
            (302, 303),
        )
        self._login(self.plain_portal)
        self.assertEqual(self.url_open("/my/trucalc/bank/orders").status_code, 404)
        self.assertNotIn("My TruCalc Requests", self.url_open("/my").text)

    def test_legacy_internal_company_user_has_no_bank_authorization(self):
        user = self.legacy_internal_bank_user
        self.assertFalse(user.share)
        self.assertTrue(user.has_group("base.group_user"))
        self.assertFalse(user.has_group("base.group_portal"))
        self.assertFalse(user.trucalc_bank_company_id)
        self.assertFalse(user._trucalc_has_bank_role())
        with self.assertRaises(AccessError):
            user._trucalc_bank_identity()

        self._login(user)
        self.assertNotIn("My TruCalc Requests", self.url_open("/my").text)
        self.assertEqual(self.url_open("/my/trucalc/bank/orders").status_code, 404)
