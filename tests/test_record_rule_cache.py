from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_record_rule_cache")
class TestRecordRuleCache(TransactionCase):
    def test_mapping_ids_are_part_of_rule_cache_identity(self):
        bank = self.env["res.company"].create({"name": "4B2A Cache Bank"})
        vendor = self.env["trucalc.vendor"].create({
            "name": "4B2A Cache Vendor", "vendor_type": "appraiser",
        })
        user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "4B2A Cache User",
            "login": "4b2a-cache-user",
            "group_ids": [Command.set([self.env.ref("base.group_portal").id])],
        })
        rule = self.env["ir.rule"].with_user(user)
        base_values = tuple(rule._compute_domain_context_values())
        self.assertEqual(base_values[-2:], (0, 0))

        user.sudo().trucalc_bank_company_id = bank
        bank_values = tuple(rule._compute_domain_context_values())
        self.assertEqual(bank_values[-2:], (bank.id, 0))

        user.sudo().write({
            "trucalc_bank_company_id": False,
            "trucalc_vendor_id": vendor.id,
        })
        vendor_values = tuple(rule._compute_domain_context_values())
        self.assertEqual(vendor_values[-2:], (0, vendor.id))
        self.assertNotEqual(base_values, bank_values)
        self.assertNotEqual(bank_values, vendor_values)
