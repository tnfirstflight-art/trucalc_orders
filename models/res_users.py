from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    trucalc_bank_company_id = fields.Many2one(
        "res.company",
        string="TruCalc Authorized Bank",
        index=True,
        help="Bank this user is authorized to represent in TruCalc.",
    )

    trucalc_vendor_id = fields.Many2one(
        "trucalc.vendor",
        string="TruCalc Vendor Organization",
        index=True,
        help="Vendor organization this user is authorized to represent in TruCalc.",
    )
