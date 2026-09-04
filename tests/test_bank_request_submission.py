from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_bank_request_submission")
class TestBankRequestSubmission(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls._user("4d1-submit-admin", "group_trucalc_admin")
        cls.ops = cls._user("4d1-submit-ops", "group_trucalc_operations")
        cls.bank_company = cls.env["res.company"].create({"name": "4D1 Submit Bank"})
        cls.other_bank = cls.env["res.company"].create({"name": "4D1 Other Bank"})
        cls.bank_admin = cls._user("4d1-submit-bank-admin", "group_bank_admin", cls.bank_company)
        cls.requestor = cls._user("4d1-submit-requestor", "group_bank_requestor", cls.bank_company)
        cls.other_requestor = cls._user("4d1-submit-other-requestor", "group_bank_requestor", cls.bank_company)
        cls.viewer = cls._user("4d1-submit-viewer", "group_bank_view_only", cls.bank_company)
        cls.foreign_admin = cls._user("4d1-submit-foreign-admin", "group_bank_admin", cls.other_bank)
        state = cls.env["res.country.state"].create({
            "name": "4D1 Submission State", "code": "DS",
            "country_id": cls.env.ref("base.us").id,
        })
        cls.area = cls.env["trucalc.service.area"].with_user(cls.admin).create({
            "state_id": state.id, "county": "Submission County",
            "service_type": "evaluation", "base_fee": 500,
        })

    @classmethod
    def _user(cls, login, group, bank=False):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login, "login": login, "email": f"{login}@example.test",
            "group_ids": [Command.set([cls.env.ref(f"trucalc_orders.{group}").id])],
            "trucalc_bank_company_id": bank.id if bank else False,
        })

    def _values(self, minimal=False, **overrides):
        values = {"borrower": "  Bank Borrower  ", "property_address": "100 Request Way"}
        if not minimal:
            values.update({
                "city": "Memphis", "zip_code": "38103", "loan_number": " LN-4001 ",
                "service_type": "evaluation", "property_type": "single_family",
                "due_date": fields.Date.to_string(fields.Date.today()),
                "inspection_contact_name": " Property Manager ",
                "inspection_contact_phone": "+1 (901) 555-0100 ext. 22",
                "inspection_contact_email": "Manager@Example.TEST",
                "notes": " Gate code available on arrival. ",
                "service_area_id": str(self.area.id),
                "service_state_id": str(self.area.state_id.id),
                "service_county": self.area.county,
            })
        values.update(overrides)
        return values

    def _create(self, actor, values=None):
        return self.env["trucalc.order"].with_user(actor)._create_bank_draft(
            values or self._values(minimal=True), actor,
        )

    def _update(self, order, actor, values=None):
        return self.env["trucalc.order"].with_user(actor)._update_bank_draft(
            order, values or self._values(), actor,
        )

    def _send(self, order, actor, values=None):
        return self.env["trucalc.order"].with_user(actor)._send_bank_draft(
            order, values or self._values(), actor,
        )

    def test_authorized_minimal_draft_creation_assigns_identity_not_order_date(self):
        for actor in (self.bank_admin, self.requestor):
            order = self._create(actor)
            self.assertEqual(order.status, "draft")
            self.assertFalse(order.order_date)
            self.assertNotEqual(order.order_number, "New")
            self.assertEqual(order.company_id, self.bank_company)
            self.assertEqual(order.requestor_company_id, self.bank_company)
            self.assertEqual(order.requestor_id, actor)
            self.assertEqual(order.create_uid, actor)
            self.assertFalse(order.service_area_id)
            self.assertTrue(order.message_ids.filtered(
                lambda message: "Bank Draft Created" in (message.body or "")
            ))

    def test_controlled_update_and_send_are_authoritative(self):
        order = self._create(self.requestor)
        self._update(order, self.requestor)
        self.assertEqual(order.status, "draft")
        self.assertEqual(
            order.inspection_contact_phone, "9015550100 ext 22",
        )
        self.assertEqual(
            order.inspection_contact_phone_display, "(901) 555-0100 ext. 22",
        )
        order.invalidate_recordset(["inspection_contact_phone"])
        reopened = self.env["trucalc.order"].browse(order.id)
        self.assertEqual(
            reopened.inspection_contact_phone, "9015550100 ext 22",
        )
        self.assertEqual(order.inspection_contact_email, "manager@example.test")
        sent = self._send(order, self.bank_admin)
        self.assertEqual(sent.status, "new")
        self.assertEqual(sent.order_date, fields.Date.context_today(sent))
        self.assertEqual(sent.service_area_id, self.area)
        self.assertEqual((sent.state, sent.county), ("DS", "Submission County"))
        self.assertEqual((sent.borrower, sent.loan_number), ("Bank Borrower", "LN-4001"))
        self.assertEqual(
            sent.inspection_contact_phone, "9015550100 ext 22",
        )
        event = sent.lifecycle_event_ids
        self.assertEqual(len(event), 1)
        self.assertEqual(event.event_type, "bank_request_sent")
        self.assertEqual((event.from_status, event.to_status), ("draft", "new"))
        self.assertEqual(event.actor_id, self.bank_admin)

    def test_inspection_contact_phone_normalization_and_validation(self):
        cases = (
            ("6625551234", "6625551234"),
            ("662-555-1234", "6625551234"),
            ("(662)555-1234", "6625551234"),
            ("1-662-555-1234", "6625551234"),
            ("+1 662 555 1234", "6625551234"),
            ("6625551234 x123", "6625551234 ext 123"),
            ("662-555-1234 ext 45", "6625551234 ext 45"),
            ("(662) 555-1234 ext. 7", "6625551234 ext 7"),
            ("662\t555\n1234", "6625551234"),
            ("  +44 20 7946 0958  ", "+44 20 7946 0958"),
            (" +49   30  901820 ", "+49 30 901820"),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                draft = self._create(
                    self.requestor,
                    self._values(
                        minimal=True, inspection_contact_phone=raw,
                    ),
                )
                self.assertEqual(draft.inspection_contact_phone, expected)
                if expected.startswith("6625551234"):
                    expected_display = "(662) 555-1234"
                    if " ext " in expected:
                        expected_display += " ext. %s" % expected.rsplit(" ", 1)[1]
                    self.assertEqual(
                        draft.inspection_contact_phone_display, expected_display,
                    )

        for unusable in ("call me", "extension only x123", "---"):
            with self.subTest(unusable=unusable), self.assertRaises(
                ValidationError
            ):
                self._create(
                    self.requestor,
                    self._values(
                        minimal=True, inspection_contact_phone=unusable,
                    ),
                )

    def test_internal_order_create_and_write_share_phone_normalization(self):
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "Internal Phone Boundary",
            "property_address": "10 Internal Way",
            "service_type": "evaluation",
            "due_date": fields.Date.add(fields.Date.today(), days=7),
            "inspection_contact_phone": "1 (662) 555-0199 x8",
        })
        self.assertEqual(
            order.inspection_contact_phone, "6625550199 ext 8",
        )
        self.assertEqual(
            order.inspection_contact_phone_display, "(662) 555-0199 ext. 8",
        )
        order.with_user(self.admin).write({
            "inspection_contact_phone": "  +44   20 7946 0958  ",
        })
        self.assertEqual(
            order.inspection_contact_phone, "+44 20 7946 0958",
        )

    def test_historical_phone_display_does_not_mutate_authoritative_storage(self):
        variants = (
            "6625551234", "662-555-1234", "(662)555-1234",
            "16625551234", "+16625551234",
        )
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "Historical Phone",
            "property_address": "12 Archive Way",
            "service_type": "evaluation",
            "due_date": fields.Date.add(fields.Date.today(), days=7),
        })
        historical_messages = [
            (message.id, message.body) for message in order.message_ids
        ]
        for historical in variants:
            with self.subTest(historical=historical):
                self.env.cr.execute(
                    "UPDATE trucalc_order SET inspection_contact_phone = %s WHERE id = %s",
                    (historical, order.id),
                )
                order.invalidate_recordset(["inspection_contact_phone"])
                self.assertEqual(
                    order.inspection_contact_phone_display, "(662) 555-1234",
                )
                self.env.cr.execute(
                    "SELECT inspection_contact_phone FROM trucalc_order WHERE id = %s",
                    (order.id,),
                )
                self.assertEqual(self.env.cr.fetchone()[0], historical)
                self.assertEqual(
                    [(message.id, message.body) for message in order.message_ids],
                    historical_messages,
                )

    def test_display_inverse_is_canonical_and_unrelated_write_does_not_touch_phone(self):
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "Display Inverse",
            "property_address": "14 Display Way",
            "service_type": "evaluation",
            "due_date": fields.Date.add(fields.Date.today(), days=7),
            "inspection_contact_phone": "662-555-1212",
        })
        order.with_user(self.admin).inspection_contact_phone_display = (
            "1 (901) 555-0102 x9"
        )
        self.assertEqual(order.inspection_contact_phone, "9015550102 ext 9")
        order.with_user(self.admin).write({"borrower": "Unrelated Edit"})
        self.assertEqual(order.inspection_contact_phone, "9015550102 ext 9")

    def test_draft_authorization_and_post_send_fail_closed(self):
        order = self._create(self.requestor)
        self.assertTrue(order.with_user(self.requestor).has_access("read"))
        self.assertTrue(order.with_user(self.bank_admin).has_access("read"))
        self.assertFalse(order.with_user(self.other_requestor).has_access("read"))
        self.assertFalse(order.with_user(self.viewer).has_access("read"))
        self.assertFalse(order.with_user(self.foreign_admin).has_access("read"))
        self.assertTrue(self._update(order, self.bank_admin))
        for actor in (self.other_requestor, self.viewer, self.foreign_admin, self.ops):
            with self.assertRaises(AccessError):
                self._update(order, actor)
            with self.assertRaises(AccessError):
                self._send(order, actor)
        self._send(order, self.requestor)
        self.assertTrue(order.with_user(self.other_requestor).has_access("read"))
        self.assertTrue(order.with_user(self.viewer).has_access("read"))
        with self.assertRaises(AccessError):
            self._update(order, self.requestor)
        with self.assertRaises(AccessError):
            self._send(order, self.requestor)
        self.assertEqual(len(order.lifecycle_event_ids), 1)

    def test_final_requiredness_and_inactive_area_revalidation(self):
        required = (
            "borrower", "property_address", "city", "zip_code", "loan_number",
            "service_type", "property_type", "due_date", "inspection_contact_name",
            "inspection_contact_phone", "inspection_contact_email", "service_area_id",
            "service_state_id", "service_county",
        )
        for field_name in required:
            order = self._create(self.requestor)
            with self.assertRaises(ValidationError), self.env.cr.savepoint():
                self._send(order, self.requestor, self._values(**{field_name: ""}))
            self.assertEqual(order.status, "draft")
        order = self._create(self.requestor)
        self.area.with_user(self.admin).active = False
        with self.assertRaises(ValidationError):
            self._send(order, self.requestor)
        self.assertEqual(order.status, "draft")

    def test_forged_fields_and_service_combinations_are_rejected(self):
        forbidden = {
            "loan_amount": 1, "company_id": self.other_bank.id,
            "requestor_company_id": self.other_bank.id, "requestor_id": self.admin.id,
            "order_number": "FORGED", "order_date": fields.Date.today(),
            "status": "completed", "bidding_round": 4, "assigned_vendor_id": 1,
            "unknown": "forged",
        }
        for key, value in forbidden.items():
            with self.assertRaises(AccessError):
                self._create(self.requestor, self._values(**{key: value}))
        order = self._create(self.requestor)
        with self.assertRaises(ValidationError):
            self._update(
                order, self.requestor,
                self._values(service_area_id="999999999"),
            )
        with self.assertRaises(ValidationError):
            self._update(
                order, self.requestor,
                self._values(service_state_id="999999999"),
            )
        with self.assertRaises(ValidationError):
            self._send(order, self.requestor, self._values(service_county="Forged"))
        with self.assertRaises(ValidationError):
            self._send(order, self.requestor, self._values(service_type="appraisal"))
        with self.assertRaises(ValidationError):
            self._send(
                order, self.requestor,
                self._values(service_area_id="999999999"),
            )

    def test_raw_bank_order_acl_end_state_and_loan_amount_policy(self):
        for actor in (self.bank_admin, self.requestor, self.viewer):
            model = self.env["trucalc.order"].with_user(actor)
            self.assertTrue(model.has_access("read"))
            for operation in ("create", "write", "unlink"):
                self.assertFalse(model.has_access(operation))
            with self.assertRaises(AccessError):
                model.create(self._values())
        order_arch = self.env.ref("trucalc_orders.view_trucalc_order_form").arch
        self.assertNotIn('name="loan_amount"', order_arch)
        self.assertNotIn("loan_amount", self.env["trucalc.vendor.order"].fields_get())
