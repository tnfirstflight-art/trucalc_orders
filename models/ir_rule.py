from odoo import api, models


class IrRule(models.Model):
    _inherit = "ir.rule"

    @api.model
    def _compute_domain_context_values(self):
        yield from super()._compute_domain_context_values()
        user = self.env.user.sudo()
        yield user.trucalc_bank_company_id.id or 0
        yield user.trucalc_vendor_id.id or 0
