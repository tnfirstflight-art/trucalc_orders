from odoo import fields, models, _
from odoo.exceptions import ValidationError


class TruCalcVendorEngagementDecisionWizard(models.TransientModel):
    _name = "trucalc.vendor.engagement.decision.wizard"
    _description = "Review Vendor Delivery Change"

    engagement_id = fields.Many2one(
        "trucalc.vendor.engagement", required=True, readonly=True,
    )
    order_number = fields.Char(
        related="engagement_id.order_id.order_number", readonly=True,
    )
    vendor_id = fields.Many2one(
        "trucalc.vendor", related="engagement_id.vendor_id", readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="engagement_id.currency_id", readonly=True,
    )
    agreed_vendor_fee = fields.Float(
        related="engagement_id.agreed_vendor_fee", readonly=True,
    )
    current_delivery_date = fields.Date(
        related="engagement_id.vendor_delivery_date", readonly=True,
    )
    requested_delivery_date = fields.Date(
        related="engagement_id.pending_request_event_id.requested_delivery_date",
        readonly=True,
    )
    vendor_reason = fields.Text(
        related="engagement_id.pending_request_event_id.reason", readonly=True,
    )
    approval_comment = fields.Text()
    rejection_reason = fields.Text()

    def action_approve(self):
        self.ensure_one()
        engagement = self.engagement_id.exists()
        if not engagement:
            raise ValidationError(_("The Vendor engagement no longer exists."))
        engagement.action_approve_delivery_change(self.approval_comment)
        return {"type": "ir.actions.act_window_close"}

    def action_reject(self):
        self.ensure_one()
        engagement = self.engagement_id.exists()
        if not engagement:
            raise ValidationError(_("The Vendor engagement no longer exists."))
        engagement.action_reject_delivery_change(self.rejection_reason)
        return {"type": "ir.actions.act_window_close"}
