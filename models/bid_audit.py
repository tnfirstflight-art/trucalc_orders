from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class TruCalcBidAudit(models.Model):
    _name = "trucalc.bid.audit"
    _description = "Immutable Bid Lifecycle Audit"
    _order = "event_at desc, id desc"

    action = fields.Selection(
        [
            ("bidding_started", "Bidding Started"),
            ("bidding_reopened", "Bidding Reopened"),
            ("invitation_created", "Invitation Created"),
            ("invitation_revoked", "Invitation Revoked"),
            ("invitation_declined", "Invitation Declined"),
            ("response_submitted", "Response Submitted"),
            ("bid_corrected", "Bid Corrected"),
            ("bid_disqualified", "Bid Disqualified"),
            ("winner_selected", "Winner Selected"),
        ],
        required=True,
        readonly=True,
        index=True,
    )
    order_id = fields.Many2one(
        "trucalc.order", required=True, readonly=True, index=True, ondelete="restrict"
    )
    invitation_id = fields.Many2one(
        "trucalc.bid.invitation", readonly=True, index=True, ondelete="restrict"
    )
    bid_id = fields.Many2one(
        "trucalc.bid", readonly=True, index=True, ondelete="restrict"
    )
    actor_id = fields.Many2one(
        "res.users", required=True, readonly=True, index=True, ondelete="restrict"
    )
    event_at = fields.Datetime(
        required=True, readonly=True, index=True, default=fields.Datetime.now
    )
    reason = fields.Text(readonly=True)
    old_values = fields.Json(readonly=True)
    new_values = fields.Json(readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True, ondelete="restrict"
    )

    @api.model
    @api.private
    def _log_event(self, action, order, invitation=False, bid=False, **values):
        vals = {
            "action": action,
            "order_id": order.id,
            "invitation_id": invitation.id if invitation else False,
            "bid_id": bid.id if bid else False,
            "actor_id": self.env.user.id,
            "event_at": fields.Datetime.now(),
            "company_id": order.company_id.id,
            **values,
        }
        return super(TruCalcBidAudit, self.sudo()).create(vals)

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Bid audit events can only be created by trusted workflows."))

    def write(self, vals):
        raise AccessError(_("Bid audit events are immutable."))

    def unlink(self):
        raise AccessError(_("Bid audit events are immutable."))
