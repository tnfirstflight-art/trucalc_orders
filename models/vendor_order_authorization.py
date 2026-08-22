from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


DEAUTHORIZATION_REASONS = [
    ("declined", "Invitation Declined"),
    ("revoked", "Invitation Revoked"),
    ("expired", "Invitation Expired"),
    ("winner_selected", "Winner Selected"),
    ("reopened", "Bidding Reopened"),
    ("cancelled", "Order Cancelled"),
    ("completed", "Order Completed"),
    ("vendor_deactivated", "Vendor Deactivated"),
    ("round_closed", "Bidding Round Closed"),
]


class TruCalcOrderVendorAuthorization(models.Model):
    _name = "trucalc.order.vendor.authorization"
    _description = "Trusted Vendor Order Authorization"
    _order = "authorized_at desc, id desc"

    _active_vendor_order_idx = models.Index(
        "(vendor_id, order_id) WHERE active IS TRUE"
    )

    order_id = fields.Many2one(
        "trucalc.order", required=True, readonly=True, index=True, ondelete="cascade"
    )
    vendor_id = fields.Many2one(
        "trucalc.vendor", required=True, readonly=True, index=True, ondelete="restrict"
    )
    company_id = fields.Many2one(
        "res.company", related="order_id.company_id", store=True, readonly=True,
        index=True,
    )
    source = fields.Selection(
        [("invitation", "Invitation"), ("assignment", "Assignment")],
        required=True, readonly=True, index=True,
    )
    invitation_id = fields.Many2one(
        "trucalc.bid.invitation", readonly=True, index=True, ondelete="restrict"
    )
    round_number = fields.Integer(required=True, readonly=True, index=True)
    active = fields.Boolean(required=True, readonly=True, default=False, index=True)
    authorized_at = fields.Datetime(required=True, readonly=True)
    expires_at = fields.Datetime(readonly=True, index=True)
    deauthorized_at = fields.Datetime(readonly=True)
    deauthorization_reason = fields.Selection(
        DEAUTHORIZATION_REASONS, readonly=True, index=True
    )

    _invitation_authorization_unique = models.UniqueIndex(
        "(invitation_id) WHERE invitation_id IS NOT NULL",
        "An invitation may create only one vendor Order authorization.",
    )
    _assignment_authorization_unique = models.UniqueIndex(
        "(order_id, vendor_id, round_number) WHERE source = 'assignment'",
        "An assignment event may create only one vendor Order authorization.",
    )
    _round_number_positive = models.Constraint(
        "CHECK(round_number > 0)", "The authorization round must be positive."
    )
    _source_structure = models.Constraint(
        "CHECK((source = 'invitation' AND invitation_id IS NOT NULL) OR "
        "(source = 'assignment' AND invitation_id IS NULL))",
        "Authorization provenance does not match its source.",
    )
    _active_state_consistency = models.Constraint(
        "CHECK((active AND deauthorized_at IS NULL AND deauthorization_reason IS NULL) OR "
        "(NOT active AND ((deauthorized_at IS NULL AND deauthorization_reason IS NULL) OR "
        "(deauthorized_at IS NOT NULL AND deauthorization_reason IS NOT NULL))))",
        "Authorization activation and deauthorization metadata are inconsistent.",
    )

    @api.constrains("order_id", "vendor_id", "source", "invitation_id", "round_number")
    def _check_provenance(self):
        for authorization in self:
            invitation = authorization.invitation_id
            if authorization.source == "invitation" and (
                invitation.order_id != authorization.order_id
                or invitation.vendor_id != authorization.vendor_id
                or invitation.round_number != authorization.round_number
                or invitation.is_legacy_reconstructed
            ):
                raise ValidationError(_("Invitation authorization provenance is invalid."))

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Vendor Order authorization is server-maintained."))

    def write(self, vals):
        raise AccessError(_("Vendor Order authorization is server-maintained."))

    def unlink(self):
        raise AccessError(_("Vendor Order authorization history cannot be deleted."))

    @api.model
    @api.private
    def _create_for_invitation(self, invitation):
        invitation.ensure_one()
        if (
            invitation.is_legacy_reconstructed
            or invitation.state != "invited"
            or invitation.order_id.status != "bid_requested"
            or invitation.round_number != invitation.order_id.bidding_round
            or not invitation.vendor_id.active
        ):
            raise ValidationError(_("The invitation cannot create Order authorization."))
        values = {
            "order_id": invitation.order_id.id,
            "vendor_id": invitation.vendor_id.id,
            "source": "invitation",
            "invitation_id": invitation.id,
            "round_number": invitation.round_number,
            "active": True,
            "authorized_at": fields.Datetime.now(),
            "expires_at": invitation.response_deadline,
        }
        return super(TruCalcOrderVendorAuthorization, self.sudo()).create(values)

    @api.model
    @api.private
    def _create_for_assignment(self, order, vendor, round_number):
        order.ensure_one()
        vendor.ensure_one()
        if (
            order.status != "assigned"
            or order.assigned_vendor_id != vendor
            or order.bidding_round != round_number
            or not vendor.active
        ):
            raise ValidationError(_("The assignment cannot create Order authorization."))
        values = {
            "order_id": order.id,
            "vendor_id": vendor.id,
            "source": "assignment",
            "round_number": round_number,
            "active": True,
            "authorized_at": fields.Datetime.now(),
        }
        return super(TruCalcOrderVendorAuthorization, self.sudo()).create(values)

    @api.model
    @api.private
    def _deactivate(self, domain, reason, when=None):
        authorizations = self.sudo().search([("active", "=", True), *domain])
        if not authorizations:
            return authorizations
        values = {
            "active": False,
            "deauthorized_at": when or fields.Datetime.now(),
            "deauthorization_reason": reason,
        }
        super(TruCalcOrderVendorAuthorization, authorizations).write(values)
        return authorizations

    @api.model
    @api.private
    def _update_invitation_expiry(self, invitation):
        authorization = self.sudo().search([
            ("invitation_id", "=", invitation.id), ("active", "=", True),
        ])
        if authorization:
            super(TruCalcOrderVendorAuthorization, authorization).write({
                "expires_at": invitation.response_deadline,
            })
        return authorization
