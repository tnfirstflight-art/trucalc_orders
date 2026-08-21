from odoo import models, fields


class TruCalcVendor(models.Model):
    _name = "trucalc.vendor"
    _description = "TruCalc Vendor"

    name = fields.Char(
        string="Vendor Name",
        required=True
    )

    vendor_type = fields.Selection(
        [
            ("appraiser", "Appraiser"),
            ("reviewer", "Reviewer"),
            ("environmental", "Environmental"),
        ],
        string="Vendor Type",
        required=True,
        default="appraiser"
    )

    email = fields.Char(
        string="Email"
    )

    phone = fields.Char(
        string="Phone"
    )

    active = fields.Boolean(
        string="Active",
        default=True
    )

    fee_schedule_ids = fields.One2many(
        "trucalc.vendor.fee",
        "vendor_id",
        string="Fee Schedule",
    )

    def write(self, vals):
        deactivated = self.filtered("active") if vals.get("active") is False else self.browse()
        result = super().write(vals)
        if deactivated:
            self.env["trucalc.order.vendor.authorization"]._deactivate(
                [("vendor_id", "in", deactivated.ids)], "vendor_deactivated"
            )
        return result
