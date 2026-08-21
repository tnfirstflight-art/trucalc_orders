from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


COMMERCIAL_FIELDS = {"option_name", "bid_amount", "turn_time_days", "notes"}


class TruCalcBid(models.Model):
    _name = "trucalc.bid"
    _description = "Vendor Bid"
    _order = "create_date desc"

    invitation_id = fields.Many2one(
        "trucalc.bid.invitation", string="Invitation", required=True,
        index=True, ondelete="restrict",
    )
    order_id = fields.Many2one(
        "trucalc.order", string="Order", related="invitation_id.order_id",
        store=True, readonly=True, required=True, index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Company", related="invitation_id.company_id",
        store=True, readonly=True, index=True,
    )
    vendor_id = fields.Many2one(
        "trucalc.vendor", string="Vendor", related="invitation_id.vendor_id",
        store=True, readonly=True, required=True, index=True,
    )
    round_number = fields.Integer(
        string="Bidding Round", related="invitation_id.round_number",
        store=True, readonly=True, required=True, index=True,
    )
    option_name = fields.Char(string="Option Name", required=True)
    bid_amount = fields.Float(string="Bid Amount", required=True)
    turn_time_days = fields.Integer(string="Turn Time (Days)")
    notes = fields.Text(string="Notes")
    status = fields.Selection(
        [("draft", "Draft"), ("submitted", "Submitted"),
         ("selected", "Selected"), ("not_selected", "Not Selected"),
         ("disqualified", "Disqualified")],
        string="Status", required=True, default="draft", index=True,
    )

    _one_selected_per_order_round = models.UniqueIndex(
        "(order_id, round_number) WHERE status = 'selected'",
        "Only one bid may be selected for an order and bidding round.",
    )

    @api.model
    @api.private
    def _require_manager(self):
        self.env["trucalc.bid.invitation"]._require_manager()

    @api.model
    @api.private
    def _validate_commercial_values(self, values, current=None):
        name = values.get("option_name", current.option_name if current else False)
        if not isinstance(name, str) or not name.strip():
            raise ValidationError(_("Each bid option requires a nonblank name."))

    @api.private
    def _validate_submission_values(self):
        for bid in self:
            if (
                not bid.option_name
                or not bid.option_name.strip()
                or bid.bid_amount <= 0
                or bid.turn_time_days <= 0
            ):
                raise ValidationError(
                    _("Every option requires a name, a positive amount, and positive turn time.")
                )

    @api.private
    def _validate_structure(self):
        for bid in self:
            invitation = bid.invitation_id
            if (
                not invitation
                or bid.order_id != invitation.order_id
                or bid.vendor_id != invitation.vendor_id
                or bid.company_id != invitation.company_id
                or bid.round_number != invitation.round_number
            ):
                raise ValidationError(_("The bid ownership structure is invalid."))

    @api.model
    @api.private
    def _controlled_create_draft(self, invitation, values):
        if set(values) - COMMERCIAL_FIELDS:
            raise AccessError(_("Only commercial bid fields may be supplied."))
        self._validate_commercial_values(values)
        vals = dict(
            values,
            invitation_id=invitation.id,
            order_id=invitation.order_id.id,
            vendor_id=invitation.vendor_id.id,
            company_id=invitation.company_id.id,
            round_number=invitation.round_number,
            status="draft",
        )
        return super(TruCalcBid, self.sudo()).create(vals)

    @api.private
    def _controlled_write(self, values):
        return super(TruCalcBid, self).write(values)

    @api.private
    def _controlled_unlink(self):
        return super(TruCalcBid, self).unlink()

    @api.model
    @api.private
    def _vendor_identity(self):
        return self.env["trucalc.bid.invitation"]._vendor_identity()

    @api.private
    def _authorized_vendor_bid(self):
        self.ensure_one()
        vendor = self._vendor_identity()
        bid = self.sudo().exists()
        if not bid or bid.vendor_id.id != vendor.id:
            raise AccessError(_("Vendor lifecycle access is not authorized."))
        return bid

    @api.model_create_multi
    def create(self, vals_list):
        vendor = self._vendor_identity()
        created = self.browse()
        for values in vals_list:
            if set(values) - (COMMERCIAL_FIELDS | {"invitation_id"}):
                raise AccessError(_("Bid ownership and lifecycle fields are server-controlled."))
            invitation_id = values.get("invitation_id")
            if not invitation_id:
                raise ValidationError(_("An invitation is required."))
            invitation = self.env["trucalc.bid.invitation"].sudo().browse(
                invitation_id
            ).exists()
            if not invitation or invitation.vendor_id.id != vendor.id:
                raise AccessError(_("Vendor lifecycle access is not authorized."))
            invitation._validate_current_active()
            if invitation.bid_ids.filtered(lambda bid: bid.status != "draft"):
                raise ValidationError(_("No new options may be added after submission."))
            created |= self._controlled_create_draft(invitation, values)
        return created

    def write(self, values):
        if set(values) - COMMERCIAL_FIELDS:
            raise AccessError(_("Bid ownership and lifecycle fields are server-controlled."))
        for original in self:
            bid = original._authorized_vendor_bid()
            bid._validate_draft_mutation()
            self._validate_commercial_values(values, current=bid)
        return super(TruCalcBid, self.sudo()).write(values)

    def unlink(self):
        raise AccessError(_("Bid options require an explicit lifecycle removal action."))

    @api.private
    def _validate_draft_mutation(self):
        for bid in self:
            bid.invitation_id._validate_current_active()
            if bid.status != "draft" or bid.invitation_id.bid_ids.filtered(
                lambda option: option.status != "draft"
            ):
                raise ValidationError(_("This draft can no longer be changed."))

    def action_vendor_edit_draft(self, values):
        bid = self._authorized_vendor_bid()
        if set(values) - COMMERCIAL_FIELDS:
            raise AccessError(_("Only commercial bid fields may be supplied."))
        bid._validate_draft_mutation()
        self._validate_commercial_values(values, current=bid)
        return bid._controlled_write(values)

    def action_support_edit_draft(self, values):
        self._require_manager()
        self.ensure_one()
        if set(values) - COMMERCIAL_FIELDS:
            raise AccessError(_("Only commercial bid fields may be supplied."))
        self._validate_draft_mutation()
        self._validate_commercial_values(values, current=self)
        return self._controlled_write(values)

    def action_vendor_remove_draft(self):
        bid = self._authorized_vendor_bid()
        bid.invitation_id._validate_current_active()
        if bid.status != "draft":
            raise ValidationError(_("Only an active draft may be removed."))
        return bid._controlled_unlink()

    def action_correct_submitted(self, values, reason):
        self._require_manager()
        self.ensure_one()
        if self.status != "submitted":
            raise ValidationError(_("Only a submitted bid may be corrected."))
        self._validate_structure()
        if not reason or not reason.strip():
            raise ValidationError(_("A correction reason is required."))
        if set(values) - COMMERCIAL_FIELDS:
            raise AccessError(_("Only commercial bid fields may be corrected."))
        self._validate_commercial_values(values, current=self)
        proposed = {
            "option_name": values.get("option_name", self.option_name),
            "bid_amount": values.get("bid_amount", self.bid_amount),
            "turn_time_days": values.get("turn_time_days", self.turn_time_days),
        }
        if proposed["bid_amount"] <= 0 or proposed["turn_time_days"] <= 0:
            raise ValidationError(_("Corrected bids must remain commercially valid."))
        old = {field: self[field] for field in values}
        self._controlled_write(values)
        self.env["trucalc.bid.audit"]._log_event(
            "bid_corrected", self.order_id, invitation=self.invitation_id, bid=self,
            reason=reason, old_values=old,
            new_values={field: self[field] for field in values},
        )
        return True

    def action_disqualify(self, reason):
        self._require_manager()
        self.ensure_one()
        if self.status != "submitted":
            raise ValidationError(_("Only a submitted bid may be disqualified."))
        self._validate_structure()
        if not reason or not reason.strip():
            raise ValidationError(_("A disqualification reason is required."))
        self._validate_submission_values()
        self._controlled_write({"status": "disqualified"})
        self.env["trucalc.bid.audit"]._log_event(
            "bid_disqualified", self.order_id, invitation=self.invitation_id,
            bid=self, reason=reason,
            old_values={"status": "submitted"},
            new_values={"status": "disqualified"},
        )
        return True

    def action_select_bid(self):
        self._require_manager()
        self.ensure_one()
        self.flush_recordset(["status", "order_id", "round_number", "invitation_id"])
        order = self.order_id
        order.flush_recordset(["status", "bidding_round"])
        self.env.cr.execute(
            "SELECT id FROM trucalc_order WHERE id = %s FOR UPDATE", (order.id,)
        )
        order.invalidate_recordset(["status", "bidding_round", "assigned_vendor_id", "vendor_fee"])
        self.invalidate_recordset(["status", "round_number", "vendor_id", "bid_amount"])
        self.invitation_id.invalidate_recordset(["state", "round_number"])
        self._validate_structure()
        if order.status != "bid_requested" or self.status != "submitted":
            raise ValidationError(_("The bid is not eligible for selection."))
        if (
            self.invitation_id.state != "invited"
            or self.round_number != order.bidding_round
            or not self.vendor_id.active
        ):
            raise ValidationError(_("The invitation is not eligible for selection."))
        current = self.search([
            ("order_id", "=", order.id),
            ("round_number", "=", order.bidding_round),
            ("status", "=", "selected"),
        ], limit=1)
        if current:
            raise ValidationError(_("A winning bid has already been selected for this round."))
        others = self.search([
            ("order_id", "=", order.id),
            ("round_number", "=", order.bidding_round),
            ("status", "=", "submitted"),
            ("id", "!=", self.id),
        ])
        if others:
            others._controlled_write({"status": "not_selected"})
        self._controlled_write({"status": "selected"})
        order._controlled_lifecycle_write({
            "assigned_vendor_id": self.vendor_id.id,
            "vendor_fee": self.bid_amount,
            "status": "assigned",
        })
        invitations = self.env["trucalc.bid.invitation"].search([
            ("order_id", "=", order.id),
            ("round_number", "=", order.bidding_round),
            ("state", "=", "invited"),
        ])
        if invitations:
            invitations._controlled_write({"state": "closed"})
        authorization_model = self.env["trucalc.order.vendor.authorization"]
        authorization_model._deactivate([
            ("order_id", "=", order.id),
            ("source", "=", "invitation"),
            ("round_number", "=", order.bidding_round),
        ], "winner_selected")
        authorization_model._create_for_assignment(
            order, self.vendor_id, order.bidding_round
        )
        self.env["trucalc.bid.audit"]._log_event(
            "winner_selected", order, invitation=self.invitation_id, bid=self,
            old_values={"status": "submitted"},
            new_values={"status": "selected", "assigned_vendor_id": self.vendor_id.id,
                        "vendor_fee": self.bid_amount},
        )
        return True
