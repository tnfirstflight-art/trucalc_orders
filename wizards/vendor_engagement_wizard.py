from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class TruCalcVendorEngagementWizard(models.TransientModel):
    _name = "trucalc.vendor.engagement.wizard"
    _description = "Confirm Vendor Engagement"

    bid_id = fields.Many2one(
        "trucalc.bid", string="Vendor Response", required=True, readonly=True,
    )
    order_id = fields.Many2one(
        "trucalc.order", related="bid_id.order_id", readonly=True,
    )
    order_number = fields.Char(
        string="Order Number", related="bid_id.order_id.order_number", readonly=True,
    )
    vendor_id = fields.Many2one(
        "trucalc.vendor", related="bid_id.vendor_id", readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="bid_id.currency_id", readonly=True,
    )
    accepted_fee = fields.Float(related="bid_id.bid_amount", readonly=True)
    vendor_delivery_date_display = fields.Char(
        string="Proposed Delivery Date",
        compute="_compute_vendor_delivery_date_display",
        readonly=True,
    )

    @api.depends("bid_id.proposed_delivery_date")
    def _compute_vendor_delivery_date_display(self):
        for wizard in self:
            delivery_date = fields.Date.to_date(wizard.bid_id.proposed_delivery_date)
            wizard.vendor_delivery_date_display = (
                delivery_date.strftime("%m/%d/%Y") if delivery_date else False
            )

    def action_engage_vendor(self):
        self.ensure_one()
        bid = self.bid_id.exists()
        if not bid:
            raise ValidationError(_("The Vendor response no longer exists."))
        bid._action_confirm_engagement()
        return {"type": "ir.actions.act_window_close"}
