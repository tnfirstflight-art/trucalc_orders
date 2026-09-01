import base64
import json

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
        cls.bank_requestor_b = cls._user(
            "bank-requestor-b", "group_bank_requestor", cls.bank_a
        )
        cls.bank_viewer = cls._user(
            "bank-viewer", "group_bank_view_only", cls.bank_a
        )
        cls.other_bank_user = cls._user(
            "other-bank", "group_bank_admin", cls.bank_b
        )
        cls.service_state = cls.env["res.country.state"].create({
            "name": "4D1 Portal State", "code": "DP",
            "country_id": cls.env.ref("base.us").id,
        })
        cls.service_area = cls.env["trucalc.service.area"].with_user(cls.admin).create({
            "state_id": cls.service_state.id, "county": "Portal County",
            "service_type": "evaluation",
        })
        cls.inactive_service_area = cls.env[
            "trucalc.service.area"
        ].with_user(cls.admin).create({
            "state_id": cls.service_state.id, "county": "Portal County",
            "service_type": "appraisal", "active": False,
        })
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
            data={
                "csrf_token": self._csrf_token(), "draft_action": "upload",
                "tag_id": tag_id,
            },
            files={"document_file": (filename, content, "application/octet-stream")},
        )

    def _complete_draft_values(self, **overrides):
        values = {
            "borrower": "Action Isolation Borrower",
            "property_address": "44 Isolated Action Way",
            "city": "Tupelo", "zip_code": "38801",
            "loan_number": "ACTION-44",
            "service_area_id": str(self.service_area.id),
            "service_state_id": str(self.service_state.id),
            "service_county": self.service_area.county,
            "service_type": self.service_area.service_type,
            "property_type": "single_family",
            "due_date": fields.Date.to_string(
                fields.Date.add(fields.Date.today(), days=7)
            ),
            "inspection_contact_name": "Action Contact",
            "inspection_contact_phone": "+1 662 555 0144",
            "inspection_contact_email": "action@example.test",
            "notes": "Action-isolated values",
        }
        values.update(overrides)
        return values

    def test_bank_home_list_detail_and_cross_bank_idor(self):
        for user in (
            self.bank_admin, self.bank_requestor, self.bank_viewer,
        ):
            self._login(user)
            home = self.url_open("/my").text
            self.assertIn("My TruCalc Requests", home)
            self.assertIn("o_trucalc_bank_portal", home)
            listing = self.url_open("/my/trucalc/bank/orders").text
            self.assertIn("o_trucalc_bank_portal", listing)
            self.assertIn(self.order_a.order_number, listing)
            self.assertIn("Approved Bank Borrower", listing)
            self.assertIn("101 Bank A Way", listing)
            self.assertNotIn(self.order_b.order_number, listing)
            self.assertNotIn("Foreign Bank Borrower", listing)
            detail = self.url_open(
                "/my/trucalc/bank/orders/%s/documents" % self.order_a.order_number
            ).text
            self.assertIn("o_trucalc_bank_portal", detail)
            self.assertIn("Approved Bank Borrower", detail)
            self.assertIn("101 Bank A Way", detail)
            self.assertEqual(
                self.url_open(
                    "/my/trucalc/bank/orders/%s/documents" % self.order_b.order_number
                ).status_code,
                404,
            )

    def test_controlled_draft_creation_send_and_prg(self):
        for user in (self.bank_admin, self.bank_requestor):
            self._login(user)
            listing = self.url_open("/my/trucalc/bank/orders").text
            self.assertIn("New Request", listing)
            form = self.url_open("/my/trucalc/bank/orders/new")
            self.assertEqual(form.status_code, 200)
            self.assertIn("o_trucalc_bank_portal", form.text)
            self.assertIn("Inspection Contact Person", form.text)
            self.assertIn("Loan Number", form.text)
            self.assertIn("Select a State", form.text)
            self.assertIn("data-service-areas", form.text)
            form_node = etree.HTML(form.text).xpath(
                "//form[contains(@class, 'o_trucalc_bank_draft_form')]"
            )[0]
            matrix = json.loads(form_node.get("data-service-areas"))
            expected_area = {
                "id": self.service_area.id,
                "state_id": self.service_state.id,
                "state_name": self.service_state.name,
                "county": self.service_area.county,
                "service_type": "evaluation",
                "service_label": "Evaluation",
            }
            self.assertIn(expected_area, matrix)
            self.assertNotIn(self.inactive_service_area.id, {
                area["id"] for area in matrix
            })
            self.assertEqual(
                {area["id"] for area in matrix},
                set(self.env["trucalc.service.area"].sudo().search([
                    ("active", "=", True),
                ]).ids),
            )
            self.assertNotIn("Loan Amount", form.text)
            response = self.url_open(
                "/my/trucalc/bank/orders/new",
                data={
                    "csrf_token": self._csrf_token(),
                    "borrower": "Portal Submitted Borrower",
                    "property_address": "401 Portal Way", "city": "Memphis",
                    "zip_code": "38103", "loan_number": "PORTAL-401",
                    "service_type": "evaluation", "property_type": "single_family",
                    "due_date": fields.Date.to_string(fields.Date.today()),
                    "inspection_contact_name": "Portal Contact",
                    "inspection_contact_phone": "+1 901 555 0141 x9",
                    "inspection_contact_email": "portal.contact@example.test",
                    "notes": "Portal instructions",
                    "service_area_id": str(self.service_area.id),
                    "service_state_id": str(self.service_state.id),
                    "service_county": self.service_area.county,
                },
                allow_redirects=False,
            )
            self.assertIn(response.status_code, (302, 303))
            order = self.env["trucalc.order"].sudo().search([
                ("loan_number", "=", "PORTAL-401"), ("requestor_id", "=", user.id),
            ])
            self.assertEqual(len(order), 1)
            self.assertEqual(order.status, "draft")
            self.assertFalse(order.order_date)
            self.assertIn(order.order_number, response.headers["Location"])
            detail = self.url_open(response.headers["Location"]).text
            self.assertIn("Draft created", detail)
            self.assertIn("Save Draft", detail)
            self.assertIn("Send Request", detail)
            self.assertIn("Portal Contact", detail)
            self.assertIn("(901) 555-0141 ext. 9", detail)
            self.assertNotIn("Loan Amount", detail)
            send = self.url_open(
                "/my/trucalc/bank/orders/%s/draft/send" % order.order_number,
                data={
                    "csrf_token": self._csrf_token(),
                    "draft_action": "send",
                    "borrower": "Portal Submitted Borrower",
                    "property_address": "401 Portal Way", "city": "Memphis",
                    "zip_code": "38103", "loan_number": "PORTAL-401",
                    "service_type": "evaluation", "property_type": "single_family",
                    "due_date": fields.Date.to_string(fields.Date.today()),
                    "inspection_contact_name": "Portal Contact",
                    "inspection_contact_phone": "+1 901 555 0141 x9",
                    "inspection_contact_email": "portal.contact@example.test",
                    "notes": "Portal instructions",
                    "service_area_id": str(self.service_area.id),
                    "service_state_id": str(self.service_state.id),
                    "service_county": self.service_area.county,
                },
                allow_redirects=False,
            )
            self.assertIn(send.status_code, (302, 303))
            self.assertEqual(order.status, "new")
            self.assertTrue(order.order_date)
            submitted = self.url_open(send.headers["Location"]).text
            self.assertIn("submitted successfully", submitted)
            self.assertIn("(901) 555-0141 ext. 9", submitted)
            self.assertNotIn("Save Draft", submitted)

        for user in (self.bank_viewer, self.plain_portal):
            self._login(user)
            self.assertEqual(
                self.url_open("/my/trucalc/bank/orders/new").status_code, 404
            )

    def test_draft_portal_visibility_is_creator_and_same_bank_admin_only(self):
        draft = self.env["trucalc.order"].with_user(
            self.bank_requestor
        )._create_bank_draft({
            "borrower": "Private Draft", "property_address": "1 Draft Way",
        }, self.bank_requestor)
        for user, visible in (
            (self.bank_requestor, True),
            (self.bank_admin, True),
            (self.bank_requestor_b, False),
            (self.bank_viewer, False),
            (self.other_bank_user, False),
        ):
            self._login(user)
            listing = self.url_open("/my/trucalc/bank/orders").text
            self.assertEqual(draft.order_number in listing, visible)
            detail = self.url_open(
                "/my/trucalc/bank/orders/%s/documents" % draft.order_number
            )
            self.assertEqual(detail.status_code, 200 if visible else 404)

    def test_minimal_draft_can_be_created_without_service_selection(self):
        self._login(self.bank_requestor)
        form = self.url_open("/my/trucalc/bank/orders/new")
        self.assertIn("Create Draft", form.text)
        response = self.url_open(
            "/my/trucalc/bank/orders/new",
            data={
                "csrf_token": self._csrf_token(),
                "borrower": "Minimal Portal Draft",
                "property_address": "3 Minimal Draft Way",
            },
            allow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))
        draft = self.env["trucalc.order"].sudo().search([
            ("borrower", "=", "Minimal Portal Draft"),
            ("requestor_id", "=", self.bank_requestor.id),
        ])
        self.assertEqual(len(draft), 1)
        self.assertEqual(draft.status, "draft")
        self.assertFalse(draft.service_area_id)

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
                "Client Due Date", "Status", "County", "Loan Number",
                "Inspection Contact Person", "Inspection Contact Phone",
                "Inspection Contact Email",
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

    def test_draft_documents_are_creator_admin_managed_and_soft_deleted(self):
        draft = self.env["trucalc.order"].with_user(
            self.bank_requestor
        )._create_bank_draft({
            "borrower": "Document Draft", "property_address": "2 Draft Way",
        }, self.bank_requestor)
        response = self._portal_upload(
            self.bank_requestor, draft, self.tag.id,
            "Draft Supporting.pdf", b"draft supporting",
        )
        self.assertEqual(response.status_code, 200)
        document = self.env["trucalc.document"].sudo().search([
            ("order_id", "=", draft.id), ("filename", "=", "Draft Supporting.pdf"),
        ])
        self.assertEqual(len(document), 1)
        self.assertEqual(document.originating_bank_id, self.bank_a)

        for user in (self.bank_requestor, self.bank_admin):
            self._login(user)
            inventory = self.url_open(
                "/my/trucalc/bank/orders/%s/documents" % draft.order_number
            ).text
            self.assertIn("Draft Supporting.pdf", inventory)
            self.assertIn(self.tag.name, inventory)
            self.assertIn(self.bank_requestor.name, inventory)
            self.assertIn("Uploaded Date", inventory)
            self.assertIn("Download", inventory)
            self.assertIn("Remove", inventory)
            self.assertEqual(self.url_open(
                "/my/trucalc/bank/orders/%s/documents/%s/download"
                % (draft.order_number, document.id)
            ).status_code, 200)

        for user in (
            self.bank_requestor_b, self.bank_viewer, self.other_bank_user,
        ):
            self._login(user)
            self.assertEqual(self.url_open(
                "/my/trucalc/bank/orders/%s/documents" % draft.order_number
            ).status_code, 404)

        self._login(self.bank_requestor_b)
        self.assertEqual(self.url_open(
            "/my/trucalc/bank/orders/%s/documents/%s/delete"
            % (draft.order_number, document.id),
            data={"csrf_token": self._csrf_token()},
        ).status_code, 404)
        self._login(self.bank_admin)
        deleted = self.url_open(
            "/my/trucalc/bank/orders/%s/documents/%s/delete"
            % (draft.order_number, document.id),
            data={"csrf_token": self._csrf_token()},
            allow_redirects=False,
        )
        self.assertIn(deleted.status_code, (302, 303))
        self.assertFalse(document.active)
        self.assertFalse(document.attachment)
        self.assertEqual(document.deleted_by_id, self.bank_admin)
        self.assertEqual(document.event_ids.sorted("id")[-1].event_type, "deleted")
        self.assertNotIn("Draft Supporting.pdf", self.url_open(
            "/my/trucalc/bank/orders/%s/documents" % draft.order_number
        ).text)

    def test_draft_upload_saves_current_form_values_before_document_creation(self):
        draft = self.env["trucalc.order"].with_user(
            self.bank_requestor
        )._create_bank_draft({
            "borrower": "Before Upload", "property_address": "2 Draft Way",
        }, self.bank_requestor)
        self._login(self.bank_requestor)
        response = self.url_open(
            "/my/trucalc/bank/orders/%s/documents/upload" % draft.order_number,
            data={
                "csrf_token": self._csrf_token(), "draft_action": "upload",
                "tag_id": self.tag.id,
                "borrower": "Saved During Upload",
                "property_address": "22 Current Form Way",
                "city": "Tupelo", "zip_code": "38801",
                "loan_number": "UPLOAD-SAVE-22",
                "service_area_id": str(self.service_area.id),
                "service_state_id": str(self.service_state.id),
                "service_county": self.service_area.county,
                "service_type": self.service_area.service_type,
                "property_type": "single_family",
                "inspection_contact_name": "Current Contact",
                "inspection_contact_phone": "+1 662 555 0122",
                "inspection_contact_email": "current@example.test",
                "notes": "Current unsaved browser values",
            },
            files={
                "document_file": (
                    "Saved With Draft.pdf", b"draft and document",
                    "application/pdf",
                ),
            },
            allow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))
        draft.invalidate_recordset()
        self.assertEqual(draft.borrower, "Saved During Upload")
        self.assertEqual(draft.property_address, "22 Current Form Way")
        self.assertEqual(draft.city, "Tupelo")
        self.assertEqual(draft.service_area_id, self.service_area)
        self.assertEqual(draft.notes, "Current unsaved browser values")
        self.assertEqual(draft.inspection_contact_phone, "6625550122")
        self.assertEqual(draft.status, "draft")
        document = self.env["trucalc.document"].sudo().search([
            ("order_id", "=", draft.id),
            ("filename", "=", "Saved With Draft.pdf"),
        ])
        self.assertEqual(len(document), 1)

    def test_draft_save_upload_and_send_actions_are_isolated(self):
        draft = self.env["trucalc.order"].with_user(
            self.bank_requestor
        )._create_bank_draft({
            "borrower": "Before Actions", "property_address": "4 Action Way",
        }, self.bank_requestor)
        self._login(self.bank_requestor)
        upload_values = self._complete_draft_values(draft_action="upload")
        upload_values.update({
            "csrf_token": self._csrf_token(), "tag_id": self.tag.id,
        })
        uploaded = self.url_open(
            "/my/trucalc/bank/orders/%s/documents/upload" % draft.order_number,
            data=upload_values,
            files={"document_file": (
                "Action Isolation.pdf", b"isolated", "application/pdf",
            )},
            allow_redirects=False,
        )
        self.assertIn(uploaded.status_code, (302, 303))
        document_domain = [
            ("order_id", "=", draft.id),
            ("filename", "=", "Action Isolation.pdf"),
        ]
        self.assertEqual(
            self.env["trucalc.document"].sudo().search_count(document_domain), 1,
        )

        save_values = self._complete_draft_values(
            draft_action="save", notes="Saved without upload",
        )
        save_values["csrf_token"] = self._csrf_token()
        saved = self.url_open(
            "/my/trucalc/bank/orders/%s/draft/save" % draft.order_number,
            data=save_values, allow_redirects=False,
        )
        self.assertIn(saved.status_code, (302, 303))
        self.assertEqual(
            self.env["trucalc.document"].sudo().search_count(document_domain), 1,
        )

        send_values = self._complete_draft_values(
            draft_action="send", inspection_contact_email="",
            notes="Unsaved send failure value",
        )
        send_values["csrf_token"] = self._csrf_token()
        rejected = self.url_open(
            "/my/trucalc/bank/orders/%s/draft/send" % draft.order_number,
            data=send_values,
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertIn("Inspection Contact Email is required", rejected.text)
        self.assertNotIn("already exists", rejected.text)
        self.assertIn("Unsaved send failure value", rejected.text)
        self.assertIn("(662) 555-0144", rejected.text)
        self.assertEqual(draft.status, "draft")
        self.assertEqual(
            self.env["trucalc.document"].sudo().search_count(document_domain), 1,
        )

        send_values.update({
            "csrf_token": self._csrf_token(),
            "inspection_contact_email": "action@example.test",
        })
        sent = self.url_open(
            "/my/trucalc/bank/orders/%s/draft/send" % draft.order_number,
            data=send_values, allow_redirects=False,
        )
        self.assertIn(sent.status_code, (302, 303))
        self.assertEqual(draft.status, "new")
        self.assertEqual(
            self.env["trucalc.document"].sudo().search_count(document_domain), 1,
        )
        submitted_page = self.url_open(sent.headers["Location"]).text
        self.assertIn("Action Isolation.pdf", submitted_page)
        self.assertIn("Download", submitted_page)
        self.assertNotIn("Remove", submitted_page)

    def test_draft_action_discriminator_and_upload_failure_fail_closed(self):
        draft = self.env["trucalc.order"].with_user(
            self.bank_requestor
        )._create_bank_draft({
            "borrower": "Discriminator Before",
            "property_address": "8 Discriminator Way",
        }, self.bank_requestor)
        self._login(self.bank_requestor)
        for route in ("save", "send"):
            response = self.url_open(
                "/my/trucalc/bank/orders/%s/draft/%s"
                % (draft.order_number, route),
                data={
                    "csrf_token": self._csrf_token(),
                    "draft_action": "forged",
                    "borrower": "Forged Mutation",
                    "property_address": "9 Forged Way",
                },
            )
            self.assertEqual(response.status_code, 404)
        forged_upload = self.url_open(
            "/my/trucalc/bank/orders/%s/documents/upload" % draft.order_number,
            data={
                "csrf_token": self._csrf_token(), "draft_action": "send",
                "tag_id": self.tag.id, "borrower": "Forged Upload Mutation",
                "property_address": "10 Forged Way",
            },
            files={"document_file": ("Forged.pdf", b"forged", "application/pdf")},
        )
        self.assertEqual(forged_upload.status_code, 404)
        draft.invalidate_recordset()
        self.assertEqual(draft.borrower, "Discriminator Before")

        self._portal_upload(
            self.bank_requestor, draft, self.tag.id,
            "Duplicate Retention.pdf", b"original",
        )
        failed_values = self._complete_draft_values(
            draft_action="upload", borrower="Retained Browser Borrower",
        )
        failed_values.update({
            "csrf_token": self._csrf_token(), "tag_id": self.tag.id,
        })
        failed = self.url_open(
            "/my/trucalc/bank/orders/%s/documents/upload" % draft.order_number,
            data=failed_values,
            files={"document_file": (
                "Duplicate Retention.pdf", b"duplicate", "application/pdf",
            )},
        )
        self.assertEqual(failed.status_code, 200)
        self.assertIn("Duplicate Retention.pdf", failed.text)
        self.assertIn("Retained Browser Borrower", failed.text)
        draft.invalidate_recordset()
        self.assertEqual(draft.borrower, "Discriminator Before")

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
