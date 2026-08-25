from odoo import Command, api, fields, models, _
from odoo.exceptions import ValidationError


class TruCalcBidRequestWizard(models.TransientModel):
    _name = "trucalc.bid.request.wizard"
    _description = "Request and Manage Vendor Bids"

    order_id = fields.Many2one("trucalc.order", required=True, readonly=True)
    mode = fields.Selection(
        [("request", "Request Bids"), ("manage", "Manage Bid Requests")],
        required=True,
        readonly=True,
    )
    response_deadline = fields.Datetime(string="Bid Response Deadline")
    line_ids = fields.One2many(
        "trucalc.bid.request.wizard.line", "wizard_id", string="Vendors"
    )

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        order = self.env["trucalc.order"].browse(values.get("order_id")).exists()
        mode = values.get("mode")
        if not order or mode not in ("request", "manage"):
            return values
        order._require_bid_manager()
        invitations = (
            order._current_round_invitations()
            if mode == "manage" else self.env["trucalc.bid.invitation"].browse()
        )
        invitation_by_vendor = {
            invitation.vendor_id.id: invitation for invitation in invitations
        }
        unknown_snapshot_ids = set()
        if invitations:
            self.env.cr.execute(
                "SELECT id FROM trucalc_bid_invitation "
                "WHERE id = ANY(%s) AND standard_fee IS NULL",
                (invitations.ids,),
            )
            unknown_snapshot_ids = {row[0] for row in self.env.cr.fetchall()}
        invited = invitations.mapped("vendor_id")
        eligible = order._eligible_solicitation_vendors()
        vendors = invited | eligible
        fee_by_vendor = {
            fee.vendor_id.id: fee.fee
            for fee in self.env["trucalc.vendor.fee"].search([
                ("vendor_id", "in", vendors.ids),
                ("service_type", "=", order.service_type),
            ])
        }
        lines = []
        for vendor in vendors.sorted("name"):
            invitation = invitation_by_vendor.get(vendor.id)
            standard_fee = (
                invitation.standard_fee if invitation else fee_by_vendor[vendor.id]
            )
            fee_known = not invitation or invitation.id not in unknown_snapshot_ids
            lines.append(Command.create({
                "vendor_id": vendor.id,
                "standard_fee": standard_fee if fee_known else False,
                "standard_fee_display": (
                    f"{standard_fee:.2f}" if fee_known else _("Not recorded")
                ),
                "already_invited": bool(invitation),
            }))
        values["line_ids"] = lines
        if mode == "manage":
            values["response_deadline"] = order._current_round_deadline()
        return values

    def action_confirm(self):
        self.ensure_one()
        self.order_id._require_bid_manager()
        vendors = self.line_ids.filtered(
            lambda line: line.selected and not line.already_invited
        ).mapped("vendor_id")
        if not vendors:
            raise ValidationError(_("Select at least one vendor before requesting bids."))
        if self.mode == "request":
            self.order_id.action_request_vendor_bids(vendors, self.response_deadline)
        elif self.mode == "manage":
            self.order_id.action_add_vendor_bid_requests(vendors)
        else:
            raise ValidationError(_("The solicitation workflow is invalid."))
        return {"type": "ir.actions.act_window_close"}


class TruCalcBidRequestWizardLine(models.TransientModel):
    _name = "trucalc.bid.request.wizard.line"
    _description = "Vendor Solicitation Selection"
    _order = "already_invited desc, vendor_id"

    wizard_id = fields.Many2one(
        "trucalc.bid.request.wizard", required=True, ondelete="cascade"
    )
    selected = fields.Boolean()
    vendor_id = fields.Many2one("trucalc.vendor", required=True, readonly=True)
    standard_fee = fields.Float(readonly=True)
    standard_fee_display = fields.Char(string="Standard Fee", readonly=True)
    already_invited = fields.Boolean(readonly=True)


class TruCalcBidDeadlineWizard(models.TransientModel):
    _name = "trucalc.bid.deadline.wizard"
    _description = "Extend Bid Response Deadline"

    order_id = fields.Many2one("trucalc.order", required=True, readonly=True)
    current_deadline = fields.Datetime(readonly=True)
    new_deadline = fields.Datetime(required=True)

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        order = self.env["trucalc.order"].browse(values.get("order_id")).exists()
        if order:
            order._require_bid_manager()
            values["current_deadline"] = order._current_round_deadline()
        return values

    def action_confirm(self):
        self.ensure_one()
        self.order_id.action_extend_bid_deadline(self.new_deadline)
        return {"type": "ir.actions.act_window_close"}
