from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_tenant_hardening")
class TestTenantHardening(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.groups = {
            name: cls.env.ref("trucalc_orders.%s" % name)
            for name in (
                "group_trucalc_admin",
                "group_trucalc_operations",
                "group_trucalc_reviewer",
                "group_bank_admin",
                "group_bank_requestor",
                "group_bank_view_only",
                "group_vendor_portal",
            )
        }
        cls.bank_a = cls.env["res.company"].create({"name": "4B1A Bank A"})
        cls.bank_b = cls.env["res.company"].create({"name": "4B1A Bank B"})
        cls.vendor_a = cls.env["trucalc.vendor"].create({
            "name": "4B1A Vendor A", "vendor_type": "appraiser",
        })
        cls.bank_requestor = cls._user(
            "4b1a-bank-requestor",
            ["group_bank_requestor"],
            bank=cls.bank_a,
        )
        cls.bank_admin = cls._user(
            "4b1a-bank-admin", ["group_bank_admin"], bank=cls.bank_a
        )
        cls.bank_viewer = cls._user(
            "4b1a-bank-viewer", ["group_bank_view_only"], bank=cls.bank_a
        )
        cls.admin = cls._user("4b1a-admin", ["group_trucalc_admin"])
        cls.ops = cls._user("4b1a-ops", ["group_trucalc_operations"])
        internal_companies = cls.env.company | cls.bank_a | cls.bank_b
        for internal_user in (cls.admin, cls.ops):
            internal_user.write({
                "company_ids": [Command.set(internal_companies.ids)]
            })
        cls.reviewer = cls._user("4b1a-reviewer", ["group_trucalc_reviewer"])
        cls.vendor_user = cls._user(
            "4b1a-vendor", ["group_vendor_portal"], vendor=cls.vendor_a
        )
        cls.order_a = cls._order(cls.admin, cls.bank_a, "Existing Bank A")
        cls.order_b = cls._order(cls.admin, cls.bank_b, "Existing Bank B")

    @classmethod
    def _user(cls, login, role_names, bank=False, vendor=False):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login,
            "login": login,
            "email": "%s@example.test" % login,
            "group_ids": [Command.set([cls.groups[name].id for name in role_names])],
            "trucalc_bank_company_id": bank.id if bank else False,
            "trucalc_vendor_id": vendor.id if vendor else False,
        })

    @classmethod
    def _order(cls, user, company, borrower="Tenant Test"):
        return cls.env["trucalc.order"].with_user(user).create({
            "borrower": borrower,
            "property_address": "1 Tenant Way",
            "company_id": company.id,
            "service_type": "evaluation",
        })

    def _assert_invalid_user(self, role_names, bank=False, vendor=False):
        with self.assertRaises(ValidationError):
            self._user(
                "invalid-%s" % self.env["ir.sequence"].next_by_code("trucalc.order"),
                role_names,
                bank=bank,
                vendor=vendor,
            )

    def test_valid_personas(self):
        valid = (
            (["group_trucalc_admin"], False, False),
            (["group_trucalc_operations"], False, False),
            (["group_trucalc_reviewer"], False, False),
            (["group_bank_admin"], self.bank_a, False),
            (["group_bank_requestor"], self.bank_a, False),
            (["group_bank_view_only"], self.bank_a, False),
            (["group_vendor_portal"], False, self.vendor_a),
        )
        for index, (roles, bank, vendor) in enumerate(valid):
            user = self._user("valid-%s" % index, roles, bank=bank, vendor=vendor)
            self.assertTrue(user)

    def test_invalid_persona_combinations(self):
        invalid = (
            (["group_trucalc_admin", "group_bank_admin"], self.bank_a, False),
            (["group_trucalc_admin", "group_vendor_portal"], False, self.vendor_a),
            (["group_trucalc_operations", "group_bank_requestor"], self.bank_a, False),
            (["group_trucalc_reviewer", "group_vendor_portal"], False, self.vendor_a),
            (["group_bank_admin", "group_vendor_portal"], self.bank_a, self.vendor_a),
            (["group_bank_admin", "group_bank_requestor"], self.bank_a, False),
            (["group_trucalc_admin", "group_trucalc_operations"], False, False),
            (["group_bank_admin"], False, False),
            (["group_vendor_portal"], False, False),
            (["group_trucalc_admin"], self.bank_a, False),
            (["group_trucalc_admin"], False, self.vendor_a),
            (["group_bank_admin"], self.bank_a, self.vendor_a),
            (["group_vendor_portal"], self.bank_a, self.vendor_a),
        )
        for roles, bank, vendor in invalid:
            self._assert_invalid_user(roles, bank=bank, vendor=vendor)

        self._assert_invalid_user([], bank=self.bank_a, vendor=self.vendor_a)

    def test_relational_group_commands_and_required_mapping_removal(self):
        with self.assertRaises(ValidationError):
            self.bank_requestor.write({
                "group_ids": [Command.link(self.groups["group_bank_admin"].id)]
            })
        with self.assertRaises(ValidationError):
            self.bank_requestor.write({"trucalc_bank_company_id": False})
        with self.assertRaises(ValidationError):
            self.vendor_user.write({"trucalc_vendor_id": False})
        with self.assertRaises(ValidationError):
            self.admin.write({"trucalc_bank_company_id": self.bank_a.id})
        with self.assertRaises(ValidationError):
            self.vendor_user.write({"trucalc_bank_company_id": self.bank_a.id})

    def test_corrupted_missing_bank_mapping_fails_closed(self):
        user = self._user(
            "4b1a-corrupt-bank", ["group_bank_requestor"], bank=self.bank_a
        )
        self.env.cr.execute(
            "UPDATE res_users SET trucalc_bank_company_id = NULL WHERE id = %s",
            (user.id,),
        )
        user.invalidate_recordset(["trucalc_bank_company_id"])
        with self.assertRaises(AccessError):
            self.env["trucalc.order"].with_user(user).create({
                "borrower": "Denied Missing Mapping",
                "property_address": "5 Tenant Way",
            })

    def test_bank_order_create_derives_trusted_ownership(self):
        model = self.env["trucalc.order"].with_user(self.bank_requestor)

        def order_values(borrower="Bank Created", **overrides):
            return {
                "borrower": borrower,
                "property_address": "2 Tenant Way",
                "service_type": "evaluation",
                **overrides,
            }

        order = model.create(order_values())
        self.assertNotEqual(order.order_number, "New")
        self.assertEqual(order.company_id, self.bank_a)
        self.assertEqual(order.requestor_company_id, self.bank_a)
        self.assertEqual(order.requestor_id, self.bank_requestor)

        correct = model.create(order_values(
            "Explicit Correct",
            company_id=self.bank_a.id,
            requestor_company_id=self.bank_a.id,
            requestor_id=self.bank_requestor.id,
        ))
        self.assertEqual(correct.company_id, self.bank_a)

        admin_order = self.env["trucalc.order"].with_user(self.bank_admin).create(
            order_values("Bank Administrator")
        )
        self.assertNotEqual(admin_order.order_number, "New")
        self.assertEqual(admin_order.company_id, self.bank_a)
        self.assertEqual(admin_order.requestor_id, self.bank_admin)

        self.assertFalse(
            self.env["ir.sequence"].with_user(self.bank_requestor).has_access("read")
        )
        self.assertFalse(
            self.env["ir.sequence"].with_user(self.bank_admin).has_access("read")
        )

        for forged in (
            {"company_id": self.bank_b.id},
            {"requestor_company_id": self.bank_b.id},
            {"requestor_id": self.admin.id},
        ):
            with self.assertRaises(AccessError):
                model.create(order_values(**forged))

    def test_bank_order_context_forgery_has_no_effect(self):
        model = self.env["trucalc.order"].with_user(self.bank_requestor).with_context(
            allowed_company_ids=[self.bank_b.id],
            default_company_id=self.bank_b.id,
            default_requestor_company_id=self.bank_b.id,
            default_requestor_id=self.admin.id,
        )
        order = model.create({
            "borrower": "Context Forgery",
            "property_address": "3 Tenant Way",
            "service_type": "evaluation",
        })
        self.assertEqual(order.company_id, self.bank_a)
        self.assertEqual(order.requestor_company_id, self.bank_a)
        self.assertEqual(order.requestor_id, self.bank_requestor)

    def test_bank_order_write_protects_ownership_and_lifecycle(self):
        order = self.order_a.with_user(self.bank_requestor)
        order.write({"borrower": "Permitted Edit"})
        self.assertEqual(order.borrower, "Permitted Edit")
        for values in (
            {"company_id": self.bank_b.id},
            {"requestor_company_id": self.bank_b.id},
            {"requestor_id": self.admin.id},
            {"bidding_round": 4},
            {"vendor_fee": 1},
        ):
            with self.assertRaises(AccessError):
                order.write(values)

    def test_view_only_cannot_create(self):
        with self.assertRaises(AccessError):
            self.env["trucalc.order"].with_user(self.bank_viewer).create({
                "borrower": "Denied", "property_address": "4 Tenant Way",
            })
        with self.assertRaises(AccessError):
            self.env["trucalc.document"].with_user(self.bank_viewer).create({
                "name": "Denied", "order_id": self.order_a.id,
            })

    def test_internal_cross_bank_creation_remains_available(self):
        for index, user in enumerate((self.admin, self.ops)):
            order = self._order(user, self.bank_b, "Internal %s" % index)
            self.assertEqual(order.company_id, self.bank_b)

    def test_bank_document_create_and_enumeration_behavior(self):
        model = self.env["trucalc.document"].with_user(self.bank_requestor)
        document = model.create({"name": "Bank A", "order_id": self.order_a.id})
        self.assertEqual(document.company_id, self.bank_a)
        self.assertEqual(document.uploaded_by, self.bank_requestor)

        messages = []
        for order_id in (self.order_b.id, 999999999):
            with self.assertRaises(AccessError) as error:
                model.create({"name": "Denied", "order_id": order_id})
            messages.append(str(error.exception))
        self.assertEqual(messages[0], messages[1])

        for forged in (
            {"company_id": self.bank_a.id},
            {"uploaded_by": self.admin.id},
            {"upload_date": fields.Datetime.now()},
        ):
            with self.assertRaises(AccessError):
                model.create({
                    "name": "Forged", "order_id": self.order_a.id, **forged,
                })

    def test_bank_document_context_forgery_has_no_effect(self):
        document = self.env["trucalc.document"].with_user(
            self.bank_requestor
        ).with_context(
            allowed_company_ids=[self.bank_b.id],
            default_company_id=self.bank_b.id,
            default_uploaded_by=self.admin.id,
        ).create({"name": "Context Safe", "order_id": self.order_a.id})
        self.assertEqual(document.company_id, self.bank_a)
        self.assertEqual(document.uploaded_by, self.bank_requestor)

    def test_document_provenance_external_and_internal_behavior(self):
        document = self.env["trucalc.document"].with_user(self.admin).create({
            "name": "Provenance", "order_id": self.order_a.id,
        })
        external_document = document.with_user(self.vendor_user)
        for values in (
            {"order_id": self.order_b.id},
            {"company_id": self.bank_b.id},
            {"uploaded_by": self.vendor_user.id},
            {"upload_date": fields.Datetime.now()},
        ):
            with self.assertRaises(AccessError):
                external_document.write(values)

        document.with_user(self.admin).write({"name": "Admin Edit"})
        document.with_user(self.ops).write({"name": "Operations Edit"})
        document.with_user(self.reviewer).write({"name": "Reviewer Edit"})
        self.assertEqual(document.name, "Reviewer Edit")
