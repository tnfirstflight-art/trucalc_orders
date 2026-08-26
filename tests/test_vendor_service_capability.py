from psycopg2.errors import UniqueViolation

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_vendor_service_capability")
class TestVendorServiceCapability(TransactionCase):
    def _vendor(self, name, legacy_type="appraiser", services=None, active=True):
        vendor = self.env["trucalc.vendor"].create({
            "name": name, "vendor_type": legacy_type, "active": active,
        })
        for service, fee in services or []:
            self.env["trucalc.vendor.fee"].create({
                "vendor_id": vendor.id, "service_type": service, "fee": fee,
            })
        return vendor

    def _order(self, service="evaluation"):
        return self.env["trucalc.order"].create({
            "borrower": "Capability Borrower",
            "property_address": "1 Capability Way",
            "service_type": service,
            "due_date": fields.Date.add(fields.Date.today(), days=14),
        })

    def test_fee_rows_are_multi_service_capability(self):
        evaluation = self._vendor("Evaluation only", "reviewer", [("evaluation", 100)])
        appraisal = self._vendor("Appraisal only", services=[("appraisal", 200)])
        review = self._vendor("Review only", "environmental", [("review", 300)])
        environmental = self._vendor("Environmental only", services=[("environmental", 400)])
        appraisal_evaluation = self._vendor(
            "Appraisal Evaluation", services=[("appraisal", 500), ("evaluation", 150)]
        )
        all_three = self._vendor(
            "Three services", services=[
                ("evaluation", 175), ("appraisal", 550), ("review", 225),
            ],
        )
        unrelated = self._vendor("Unrelated", services=[("review", 50)])
        inactive = self._vendor("Inactive", services=[("evaluation", 75)], active=False)

        expected = {
            "evaluation": {evaluation, appraisal_evaluation, all_three},
            "appraisal": {appraisal, appraisal_evaluation, all_three},
            "review": {review, all_three, unrelated},
            "environmental": {environmental},
        }
        for service, vendors in expected.items():
            eligible = set(self._order(service)._eligible_solicitation_vendors())
            self.assertTrue(vendors <= eligible)
            self.assertNotIn(inactive, eligible)
            if service != "review":
                self.assertNotIn(unrelated, eligible)
        self.assertNotIn(inactive, self._order()._eligible_solicitation_vendors())

    def test_vendor_service_fee_is_database_unique_and_zero_is_valid(self):
        vendor = self._vendor("Unique", services=[("evaluation", 0)])
        self.assertIn(vendor, self._order()._eligible_solicitation_vendors())
        with self.assertRaises(UniqueViolation), self.cr.savepoint():
            self.env["trucalc.vendor.fee"].create({
                "vendor_id": vendor.id, "service_type": "evaluation", "fee": 1,
            })

    def test_reviewer_capability_is_server_enforced(self):
        capable = self._vendor(
            "Appraiser and Reviewer", "appraiser",
            [("appraisal", 500), ("review", 200)],
        )
        incapable = self._vendor("Legacy Reviewer Without Review", "reviewer", [("evaluation", 100)])
        order = self._order()
        order.reviewer_id = capable
        order._onchange_reviewer_fee()
        self.assertEqual(order.review_fee, 200)
        order._controlled_lifecycle_write({"status": "report_received"})
        order.action_assign_reviewer()
        self.assertEqual(order.status, "reviewer_assigned")
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._order().reviewer_id = incapable

    def test_capability_does_not_create_authorization(self):
        vendor = self._vendor("No Self Authorization", services=[("evaluation", 100)])
        order = self._order()
        self.assertFalse(self.env["trucalc.order.vendor.authorization"].sudo().search([
            ("vendor_id", "=", vendor.id), ("order_id", "=", order.id),
        ]))
        vendor.fee_schedule_ids.fee = 125
        self.assertFalse(self.env["trucalc.order.vendor.authorization"].sudo().search([
            ("vendor_id", "=", vendor.id), ("order_id", "=", order.id),
        ]))
