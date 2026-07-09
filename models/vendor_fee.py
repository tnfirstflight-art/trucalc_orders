from odoo import models, fields, api
from odoo.exceptions import ValidationError


class VendorFee(models.Model):
    _name = "trucalc.vendor.fee"
    _description = "Vendor Fee Schedule"

    _sql_constraints = [
        (
            "vendor_service_unique",
            "unique(vendor_id, service_type)",
            "A vendor may only have one fee schedule per service type.",
        )
    ]

    vendor_id = fields.Many2one(
        "trucalc.vendor",
        string="Vendor",
        required=True,
        ondelete="cascade",
    )

    service_type = fields.Selection(
        [
            ("evaluation", "Evaluation"),
            ("appraisal", "Appraisal"),
            ("review", "Review"),
            ("environmental", "Environmental"),
        ],
        string="Service Type",
        required=True,
    )

    fee = fields.Float(
        string="Fee",
        required=True,
    )

    @api.constrains("vendor_id", "service_type")
    def _check_unique_service_type(self):
        for record in self:

            existing = self.search(
                [
                    ("vendor_id", "=", record.vendor_id.id),
                    ("service_type", "=", record.service_type),
                    ("id", "!=", record.id),
                ],
                limit=1,
            )

            if existing:
                raise ValidationError(
                    "This vendor already has a fee defined for that service type."
                )