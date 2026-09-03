from odoo import fields, models


class TruCalcValuationRevisionWizard(models.TransientModel):
    _name = "trucalc.valuation.revision.wizard"
    _description = "Request Valuation Revision"

    order_id = fields.Many2one(
        "trucalc.order", required=True, readonly=True, ondelete="cascade",
    )
    target_valuation_id = fields.Many2one(
        "trucalc.vendor.deliverable", string="Current Valuation",
        required=True, readonly=True, ondelete="cascade",
    )
    vendor_revision_instructions = fields.Text(
        string="Vendor-facing Revision Instructions", required=True,
    )

    def action_request_revision(self):
        self.ensure_one()
        self.order_id.action_request_valuation_revision(
            self.target_valuation_id, self.vendor_revision_instructions,
        )
        return {"type": "ir.actions.act_window_close"}
