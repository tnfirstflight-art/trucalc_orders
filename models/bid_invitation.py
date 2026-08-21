from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


MANAGER_GROUPS = (
    "trucalc_orders.group_trucalc_admin",
    "trucalc_orders.group_trucalc_operations",
)
COMMERCIAL_FIELDS = {"option_name", "bid_amount", "turn_time_days", "notes"}


class TruCalcBidInvitation(models.Model):
    _name = "trucalc.bid.invitation"
    _description = "Vendor Bid Invitation"
    _order = "order_id, round_number desc, vendor_id"

    order_id = fields.Many2one(
        "trucalc.order", string="Order", required=True, index=True, ondelete="cascade"
    )
    vendor_id = fields.Many2one(
        "trucalc.vendor", string="Vendor", required=True, index=True, ondelete="restrict"
    )
    company_id = fields.Many2one(
        "res.company", string="Company", related="order_id.company_id",
        store=True, readonly=True, index=True,
    )
    round_number = fields.Integer(string="Bidding Round", required=True, default=1)
    state = fields.Selection(
        [("invited", "Invited"), ("declined", "Declined"),
         ("revoked", "Revoked"), ("expired", "Expired"), ("closed", "Closed")],
        string="State", required=True, default="invited",
    )
    response_deadline = fields.Datetime(string="Response Deadline")
    invited_by = fields.Many2one("res.users", string="Invited By", readonly=True)
    invited_at = fields.Datetime(string="Invited At", readonly=True)
    declined_at = fields.Datetime(string="Declined At", readonly=True)
    revoked_at = fields.Datetime(string="Revoked At", readonly=True)
    is_legacy_reconstructed = fields.Boolean(
        string="Legacy Reconstructed", default=False, readonly=True
    )
    bid_ids = fields.One2many("trucalc.bid", "invitation_id", string="Bid Options")

    _order_vendor_round_unique = models.Constraint(
        "UNIQUE(order_id, vendor_id, round_number)",
        "A vendor may only have one invitation per order and bidding round.",
    )
    _round_number_positive = models.Constraint(
        "CHECK(round_number > 0)", "The bidding round must be greater than zero."
    )

    @api.model
    @api.private
    def _is_manager(self):
        return any(self.env.user.has_group(group) for group in MANAGER_GROUPS)

    @api.model
    @api.private
    def _require_manager(self):
        if not self._is_manager():
            raise AccessError(_("Only TruCalc bid managers may perform this operation."))

    @api.model
    @api.private
    def _vendor_identity(self):
        user = self.env.user
        if not user.has_group("trucalc_orders.group_vendor_portal"):
            raise AccessError(_("Vendor lifecycle access is not authorized."))
        if self._is_manager():
            raise AccessError(_("Mixed internal and vendor personas are not permitted."))
        if not user.trucalc_vendor_id:
            raise AccessError(_("Vendor lifecycle access is not authorized."))
        return user.trucalc_vendor_id

    @api.private
    def _authorized_vendor_invitation(self):
        self.ensure_one()
        vendor = self._vendor_identity()
        invitation = self.sudo().exists()
        if not invitation or invitation.vendor_id.id != vendor.id:
            raise AccessError(_("Vendor lifecycle access is not authorized."))
        return invitation

    @api.private
    def _lock_for_lifecycle(self):
        self.ensure_one()
        self.flush_recordset(["state", "response_deadline", "round_number", "order_id"])
        self.env.cr.execute(
            "SELECT id FROM trucalc_bid_invitation WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        self.invalidate_recordset(["state", "response_deadline", "round_number", "order_id"])

    @api.private
    def _validate_current_active(self):
        self.ensure_one()
        self._lock_for_lifecycle()
        now = fields.Datetime.now()
        if (
            self.state != "invited"
            or self.order_id.status != "bid_requested"
            or self.round_number != self.order_id.bidding_round
            or not self.vendor_id.active
            or (self.response_deadline and now > self.response_deadline)
        ):
            raise ValidationError(_("This invitation is not open for a response."))

    @api.model_create_multi
    def create(self, vals_list):
        self._require_manager()
        protected = {
            "company_id", "round_number", "state", "invited_by", "invited_at",
            "declined_at", "revoked_at", "is_legacy_reconstructed",
        }
        prepared = []
        order_ids = []
        for incoming in vals_list:
            if protected.intersection(incoming):
                raise AccessError(_("Invitation lifecycle fields are server-controlled."))
            if not incoming.get("order_id") or not incoming.get("vendor_id"):
                raise ValidationError(_("An order and vendor are required."))
            order_ids.append(incoming["order_id"])
        orders = self.env["trucalc.order"].browse(sorted(set(order_ids))).exists()
        if len(orders) != len(set(order_ids)):
            raise ValidationError(_("A valid active vendor and order are required."))
        orders.flush_recordset(["status", "bidding_round"])
        self.env.cr.execute(
            "SELECT id FROM trucalc_order WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
            (orders.ids,),
        )
        orders.invalidate_recordset(["status", "bidding_round"])
        for incoming in vals_list:
            order = orders.filtered(lambda candidate: candidate.id == incoming["order_id"])
            vendor = self.env["trucalc.vendor"].browse(incoming["vendor_id"]).exists()
            if not order or not vendor or not vendor.active:
                raise ValidationError(_("A valid active vendor and order are required."))
            expected_type = {
                "evaluation": "appraiser", "appraisal": "appraiser",
                "review": "reviewer", "environmental": "environmental",
            }.get(order.service_type)
            if order.status != "bid_requested" or order.bidding_round <= 0:
                raise ValidationError(_("The order is not in an active bidding round."))
            if not expected_type or vendor.vendor_type != expected_type:
                raise ValidationError(_("The vendor is not compatible with this service."))
            if self.search_count([
                ("order_id", "=", order.id),
                ("vendor_id", "=", vendor.id),
                ("round_number", "=", order.bidding_round),
            ]):
                raise ValidationError(
                    _("This vendor is already invited for the current bidding round.")
                )
            vals = dict(incoming)
            vals.update({
                "round_number": order.bidding_round,
                "state": "invited",
                "invited_by": self.env.user.id,
                "invited_at": fields.Datetime.now(),
                "is_legacy_reconstructed": False,
            })
            prepared.append(vals)
        invitations = super().create(prepared)
        for invitation in invitations:
            self.env["trucalc.order.vendor.authorization"]._create_for_invitation(
                invitation
            )
            self.env["trucalc.bid.audit"]._log_event(
                "invitation_created", invitation.order_id, invitation=invitation,
                new_values={"vendor_id": invitation.vendor_id.id,
                            "round_number": invitation.round_number},
            )
        return invitations

    def write(self, vals):
        raise AccessError(_("Invitation changes require an explicit lifecycle action."))

    @api.private
    def _controlled_write(self, vals):
        return super(TruCalcBidInvitation, self).write(vals)

    def unlink(self):
        raise AccessError(_("Invitation history cannot be deleted."))

    def action_set_response_deadline(self, deadline):
        self._require_manager()
        for invitation in self:
            invitation._lock_for_lifecycle()
            if invitation.state != "invited":
                raise ValidationError(_("Only an open invitation deadline may be changed."))
            super(TruCalcBidInvitation, invitation).write({"response_deadline": deadline})
            if deadline and fields.Datetime.now() > fields.Datetime.to_datetime(deadline):
                invitation._expire_locked(fields.Datetime.now())
            else:
                self.env["trucalc.order.vendor.authorization"]._update_invitation_expiry(
                    invitation
                )
        return True

    def action_revoke(self):
        self._require_manager()
        now = fields.Datetime.now()
        for invitation in self:
            invitation._lock_for_lifecycle()
            if invitation.state != "invited":
                raise ValidationError(_("Only an open invitation may be revoked."))
            if invitation.bid_ids.filtered(lambda bid: bid.status != "draft"):
                raise ValidationError(_("An invitation with a submitted or final bid cannot be revoked."))
        super(TruCalcBidInvitation, self).write({"state": "revoked", "revoked_at": now})
        self.env["trucalc.order.vendor.authorization"]._deactivate(
            [("invitation_id", "in", self.ids)], "revoked", now
        )
        for invitation in self:
            self.env["trucalc.bid.audit"]._log_event(
                "invitation_revoked", invitation.order_id, invitation=invitation
            )
        return True

    def action_vendor_decline(self):
        invitation = self._authorized_vendor_invitation()
        invitation._lock_for_lifecycle()
        if invitation.state != "invited" or invitation.bid_ids.filtered(
            lambda bid: bid.status != "draft"
        ):
            raise ValidationError(_("The invitation cannot be declined."))
        drafts = invitation.bid_ids.filtered(lambda bid: bid.status == "draft")
        if drafts:
            drafts._controlled_unlink()
        super(TruCalcBidInvitation, invitation).write(
            {"state": "declined", "declined_at": fields.Datetime.now()}
        )
        self.env["trucalc.order.vendor.authorization"]._deactivate(
            [("invitation_id", "=", invitation.id)], "declined"
        )
        self.env["trucalc.bid.audit"]._log_event(
            "invitation_declined", invitation.order_id, invitation=invitation
        )
        return True

    def action_vendor_create_option(self, values):
        invitation = self._authorized_vendor_invitation()
        invitation._validate_current_active()
        if set(values) - COMMERCIAL_FIELDS:
            raise AccessError(_("Only commercial bid fields may be supplied."))
        if invitation.bid_ids.filtered(lambda bid: bid.status != "draft"):
            raise ValidationError(_("No new options may be added after submission."))
        return self.env["trucalc.bid"]._controlled_create_draft(invitation, values)

    def action_support_create_option(self, values):
        self._require_manager()
        self.ensure_one()
        self._validate_current_active()
        if set(values) - COMMERCIAL_FIELDS:
            raise AccessError(_("Only commercial bid fields may be supplied."))
        if self.bid_ids.filtered(lambda bid: bid.status != "draft"):
            raise ValidationError(_("No new options may be added after submission."))
        return self.env["trucalc.bid"]._controlled_create_draft(self, values)

    def action_vendor_submit(self):
        invitation = self._authorized_vendor_invitation()
        invitation._validate_current_active()
        bids = invitation.bid_ids
        if not bids or any(bid.status != "draft" for bid in bids):
            raise ValidationError(_("All response options must be draft and submitted together."))
        bids._validate_submission_values()
        bids._controlled_write({"status": "submitted"})
        self.env["trucalc.bid.audit"]._log_event(
            "response_submitted", invitation.order_id, invitation=invitation,
            new_values={"bid_ids": bids.ids},
        )
        return True

    @api.private
    def _expire_locked(self, now):
        self.ensure_one()
        if (
            self.is_legacy_reconstructed
            or self.state != "invited"
            or not self.response_deadline
            or now <= self.response_deadline
        ):
            return False
        super(TruCalcBidInvitation, self).write({"state": "expired"})
        self.env["trucalc.order.vendor.authorization"]._deactivate(
            [("invitation_id", "=", self.id)], "expired", now
        )
        self.env["trucalc.bid.audit"]._log_event(
            "invitation_expired", self.order_id, invitation=self
        )
        return True

    @api.model
    @api.private
    def _cron_expire_vendor_order_authorizations(self):
        now = fields.Datetime.now()
        candidates = self.sudo().search([
            ("is_legacy_reconstructed", "=", False),
            ("state", "=", "invited"),
            ("response_deadline", "!=", False),
            ("response_deadline", "<", now),
        ])
        for invitation in candidates:
            invitation._lock_for_lifecycle()
            invitation._expire_locked(fields.Datetime.now())
        return True
