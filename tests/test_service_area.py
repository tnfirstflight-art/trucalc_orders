from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from lxml import etree


@tagged("post_install", "-at_install", "trucalc_service_area")
class TestServiceArea(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls._user("4d1-area-admin", "group_trucalc_admin")
        cls.ops = cls._user("4d1-area-ops", "group_trucalc_operations")
        cls.reviewer = cls._user("4d1-area-reviewer", "group_trucalc_reviewer")
        cls.bank_company = cls.env["res.company"].with_context(trucalc_test_bank_fixture=True).create({"name": "4D1 Area Bank", "trucalc_is_bank": True, "trucalc_bank_active": True})
        cls.bank = cls._user(
            "4d1-area-bank", "group_bank_requestor", bank=cls.bank_company,
        )
        cls.vendor = cls.env["trucalc.vendor"].create({"name": "4D1 Area Vendor"})
        cls.vendor_user = cls._user(
            "4d1-area-vendor", "group_vendor_portal", vendor=cls.vendor,
        )
        country = cls.env.ref("base.us")
        cls.state_a = cls.env["res.country.state"].create({
            "name": "4D1 State A", "code": "D1", "country_id": country.id,
        })
        cls.state_b = cls.env["res.country.state"].create({
            "name": "4D1 State B", "code": "D2", "country_id": country.id,
        })

    @classmethod
    def _user(cls, login, group, bank=False, vendor=False):
        groups = [group]
        if group == "group_trucalc_reviewer":
            groups.append("group_trucalc_operations")
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login, "login": login, "email": f"{login}@example.test",
            "group_ids": [Command.set([
                cls.env.ref(f"trucalc_orders.{name}").id for name in groups
            ])],
            "trucalc_bank_company_id": bank.id if bank else False,
            "company_id": bank.id if bank else cls.env.company.id,
            "company_ids": [Command.set(bank.ids if bank else cls.env.company.ids)],
            "trucalc_vendor_id": vendor.id if vendor else False,
        })

    def test_admin_crud_archive_and_unlink_policy(self):
        model = self.env["trucalc.service.area"].with_user(self.admin)
        area = model.create({
            "state_id": self.state_a.id, "county": "  Shelby   County ",
            "service_type": "evaluation",
        })
        self.assertEqual((area.county, area.county_normalized), (
            "Shelby County", "shelby county",
        ))
        self.assertTrue(area.active)
        self.assertEqual(area.currency_id, self.env.ref("base.USD"))
        self.assertFalse(area.base_fee)
        area.base_fee = 425
        self.assertEqual(area.base_fee, 425)
        with self.assertRaises(ValidationError):
            area.base_fee = -1
        area.write({"county": "Davidson County"})
        area.active = False
        self.assertFalse(area.active)
        with self.assertRaises(AccessError):
            area.unlink()

    def test_acl_is_admin_maintained_and_internal_read_only(self):
        for user in (self.ops, self.reviewer):
            model = self.env["trucalc.service.area"].with_user(user)
            self.assertTrue(model.has_access("read"))
            for operation in ("create", "write", "unlink"):
                self.assertFalse(model.has_access(operation))
        for user in (self.bank, self.vendor_user):
            model = self.env["trucalc.service.area"].with_user(user)
            for operation in ("read", "create", "write", "unlink"):
                self.assertFalse(model.has_access(operation))

    def test_normalized_uniqueness_and_geographic_service_identity(self):
        model = self.env["trucalc.service.area"].with_user(self.admin)
        model.create({
            "state_id": self.state_a.id, "county": "Shelby County",
            "service_type": "evaluation",
        })
        for duplicate in ("shelby county", " Shelby   County "):
            with self.assertRaises(ValidationError):
                with self.env.cr.savepoint():
                    model.create({
                        "state_id": self.state_a.id, "county": duplicate,
                        "service_type": "evaluation",
                    })
        self.assertTrue(model.create({
            "state_id": self.state_b.id, "county": "Shelby County",
            "service_type": "evaluation",
        }))
        self.assertTrue(model.create({
            "state_id": self.state_a.id, "county": "Shelby County",
            "service_type": "appraisal",
        }))

    def test_administration_list_and_create_modal_contract(self):
        model = self.env["trucalc.service.area"]
        self.assertNotIn("sequence", model._fields)
        self.assertEqual(
            model._order, "state_id, county_normalized, service_type, id",
        )

        list_view = self.env.ref(
            "trucalc_orders.view_trucalc_service_area_list"
        )
        list_arch = etree.fromstring(list_view.arch.encode())
        list_node = list_arch.xpath("//list")[0]
        self.assertEqual(list_node.get("create"), "false")
        self.assertEqual(list_node.get("delete"), "false")
        self.assertEqual(list_node.get("editable"), "bottom")
        self.assertEqual(
            list_arch.xpath("//list/field/@name"),
            ["state_id", "county", "service_type", "currency_id", "base_fee", "active"],
        )
        for field_name in ("state_id", "county", "service_type"):
            self.assertEqual(
                list_arch.xpath(
                    f"//field[@name='{field_name}']/@readonly"
                ),
                ["1"],
            )
        self.assertEqual(
            list_arch.xpath("//field[@name='active']/@widget"),
            ["boolean_toggle"],
        )
        self.assertEqual(
            list_arch.xpath("//field[@name='active']/@options"),
            ["{'autosave': True}"],
        )
        self.assertEqual(list_arch.xpath("//header/button/@string"), ["New"])

        form_view = self.env.ref(
            "trucalc_orders.view_trucalc_service_area_form"
        )
        form_arch = etree.fromstring(form_view.arch.encode())
        self.assertEqual(
            form_arch.xpath("//form//field/@name"),
            ["state_id", "county", "service_type", "currency_id", "base_fee"],
        )
        action = self.env.ref(
            "trucalc_orders.action_trucalc_service_area_new"
        )
        self.assertEqual(action.target, "new")
        self.assertEqual(action.view_mode, "form")

        list_action = self.env.ref(
            "trucalc_orders.action_trucalc_service_areas"
        )
        self.assertEqual(list_action.view_mode, "list")
