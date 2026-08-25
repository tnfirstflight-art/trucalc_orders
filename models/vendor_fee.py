from odoo import fields, models


SERVICE_SELECTION = [
    ("evaluation", "Evaluation"),
    ("appraisal", "Appraisal"),
    ("review", "Review"),
    ("environmental", "Environmental"),
]


class VendorFee(models.Model):
    _name = "trucalc.vendor.fee"
    _description = "Vendor Fee Schedule"

    _vendor_service_unique = models.Constraint(
        "UNIQUE(vendor_id, service_type)",
        "A vendor may only have one standard fee per service.",
    )

    vendor_id = fields.Many2one(
        "trucalc.vendor",
        string="Vendor",
        required=True,
        ondelete="cascade",
    )

    service_type = fields.Selection(
        SERVICE_SELECTION,
        string="Service Type",
        required=True,
    )

    fee = fields.Float(
        string="Fee",
        required=True,
    )
