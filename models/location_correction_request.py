from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from .vendor_fee import SERVICE_SELECTION


class LocationCorrectionRequest(models.Model):
    _name = "trucalc.location.correction.request"
    _description = "Staged Property Location Correction"
    _order = "id desc"
    _rec_name = "order_id"

    order_id = fields.Many2one(
        "trucalc.order", required=True, readonly=True, index=True,
        ondelete="restrict",
    )
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True,
        ondelete="restrict",
    )
    state = fields.Selection(
        [("pending", "Pending"), ("applied", "Applied"),
         ("declined", "Declined")],
        required=True, readonly=True, index=True,
    )
    old_state_id = fields.Many2one(
        "res.country.state", string="Old State Record", required=True,
        readonly=True, ondelete="restrict",
    )
    old_state = fields.Char(required=True, readonly=True)
    old_county = fields.Char(required=True, readonly=True)
    old_service_area_id = fields.Many2one(
        "trucalc.service.area", required=True, readonly=True,
        ondelete="restrict",
    )
    proposed_state_id = fields.Many2one(
        "res.country.state", string="Proposed State Record", required=True,
        readonly=True, ondelete="restrict",
    )
    proposed_state = fields.Char(required=True, readonly=True)
    proposed_county = fields.Char(required=True, readonly=True)
    proposed_service_area_id = fields.Many2one(
        "trucalc.service.area", required=True, readonly=True,
        ondelete="restrict",
    )
    service_type = fields.Selection(
        SERVICE_SELECTION, required=True, readonly=True,
    )
    original_service_area_id = fields.Many2one(
        "trucalc.service.area", required=True, readonly=True,
        ondelete="restrict",
    )
    prior_effective_fee = fields.Monetary(required=True, readonly=True)
    proposed_schedule_fee = fields.Monetary(required=True, readonly=True)
    pricing_source = fields.Selection(
        [("base", "Base"), ("negotiated", "Negotiated")],
        required=True, readonly=True,
    )
    negotiated_fee_id = fields.Many2one(
        "trucalc.negotiated.fee", readonly=True, ondelete="restrict",
    )
    currency_id = fields.Many2one(
        "res.currency", required=True, readonly=True, ondelete="restrict",
    )
    reason = fields.Text(required=True, readonly=True)
    requested_by_id = fields.Many2one(
        "res.users", required=True, readonly=True, ondelete="restrict",
    )
    requested_at = fields.Datetime(required=True, readonly=True)
    decided_by_id = fields.Many2one(
        "res.users", readonly=True, ondelete="restrict",
    )
    decided_at = fields.Datetime(readonly=True)
    fee_change_request_ids = fields.One2many(
        "trucalc.fee.change.request", "location_correction_request_id",
        string="Linked Fee Change Requests", readonly=True,
    )
    fee_change_request_id = fields.Many2one(
        "trucalc.fee.change.request", compute="_compute_fee_change_request",
        compute_sudo=True, readonly=True,
    )

    _one_pending = models.UniqueIndex(
        "(order_id) WHERE state = 'pending'",
        "An Order may have only one pending location correction.",
    )

    @api.depends("fee_change_request_ids")
    def _compute_fee_change_request(self):
        for correction in self:
            correction.fee_change_request_id = correction.fee_change_request_ids[:1]

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_(
            "Location correction requests require the controlled workflow."
        ))

    def write(self, vals):
        raise AccessError(_(
            "Location correction requests are immutable outside the decision workflow."
        ))

    def unlink(self):
        raise AccessError(_("Location correction requests cannot be deleted."))

    @api.model
    @api.private
    def _stage(self, order, actor, area, county, current, pricing, reason):
        old_area = order.service_area_id
        correction = super(LocationCorrectionRequest, self.sudo()).create({
            "order_id": order.id,
            "company_id": order.company_id.id,
            "state": "pending",
            "old_state_id": old_area.state_id.id,
            "old_state": order.state,
            "old_county": order.county,
            "old_service_area_id": old_area.id,
            "proposed_state_id": area.state_id.id,
            "proposed_state": area.state_id.code or area.state_id.name,
            "proposed_county": county,
            "proposed_service_area_id": area.id,
            "service_type": order.service_type,
            "original_service_area_id": order.original_service_area_id.id,
            "prior_effective_fee": current["amount"],
            "proposed_schedule_fee": pricing["agreed_fee"],
            "pricing_source": pricing["fee_source"],
            "negotiated_fee_id": pricing["negotiated_fee_id"],
            "currency_id": pricing["fee_currency_id"],
            "reason": reason,
            "requested_by_id": actor.id,
            "requested_at": fields.Datetime.now(),
        })
        fee_request = self.env["trucalc.fee.change.request"]._request(
            order, pricing["agreed_fee"], reason,
            location_correction_request=correction,
        )
        self.env["trucalc.order.lifecycle.event"]._log_location_correction_request(
            correction, fee_request,
        )
        return correction

    @api.model
    @api.private
    def _record_same_fee_resolution(
        self, order, actor, area, county, current, pricing, reason,
    ):
        old_area = order.service_area_id
        now = fields.Datetime.now()
        return super(LocationCorrectionRequest, self.sudo()).create({
            "order_id": order.id,
            "company_id": order.company_id.id,
            "state": "applied",
            "old_state_id": old_area.state_id.id,
            "old_state": order.state,
            "old_county": order.county,
            "old_service_area_id": old_area.id,
            "proposed_state_id": area.state_id.id,
            "proposed_state": area.state_id.code or area.state_id.name,
            "proposed_county": county,
            "proposed_service_area_id": area.id,
            "service_type": order.service_type,
            "original_service_area_id": order.original_service_area_id.id,
            "prior_effective_fee": current["amount"],
            "proposed_schedule_fee": pricing["agreed_fee"],
            "pricing_source": pricing["fee_source"],
            "negotiated_fee_id": pricing["negotiated_fee_id"],
            "currency_id": pricing["fee_currency_id"],
            "reason": reason,
            "requested_by_id": actor.id,
            "requested_at": now,
            "decided_by_id": actor.id,
            "decided_at": now,
        })

    @api.private
    def _lock(self):
        self.ensure_one()
        self.flush_recordset()
        self.env.cr.execute(
            "SELECT id FROM trucalc_location_correction_request "
            "WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        self.invalidate_recordset()
        return self

    @api.private
    def _validate_link(self, order, fee_request):
        self.ensure_one()
        correction = self.sudo()
        if (
            correction.state != "pending"
            or correction.order_id != order
            or correction.company_id != order.company_id
            or correction.fee_change_request_ids != fee_request
            or fee_request.location_correction_request_id != correction
            or correction.currency_id != fee_request.currency_id
            or correction.prior_effective_fee != fee_request.prior_fee
            or correction.proposed_schedule_fee != fee_request.proposed_fee
        ):
            raise ValidationError(_(
                "The staged location correction linkage is inconsistent."
            ))
        return correction

    @api.private
    def _validate_approval(self, order, fee_request):
        correction = self._validate_link(order, fee_request)
        current = order._validate_location_correction_eligibility(
            allowed_pending_fee_request=fee_request,
        )
        if order.status != "accepted":
            raise ValidationError(_(
                "Higher-fee location correction approval requires an Accepted Order."
            ))
        if (
            order.state != correction.old_state
            or order.county != correction.old_county
            or order.service_area_id != correction.old_service_area_id
            or order.original_service_area_id != correction.original_service_area_id
            or order.service_type != correction.service_type
            or current["amount"] != correction.prior_effective_fee
        ):
            raise ValidationError(_(
                "The Order no longer matches the staged location correction basis."
            ))
        area, county = order._resolve_service_area(
            correction.proposed_state_id,
            correction.proposed_county,
            correction.service_type,
        )
        if area != correction.proposed_service_area_id or county != correction.proposed_county:
            raise ValidationError(_(
                "The proposed Service Area is no longer available. Create a new correction."
            ))
        pricing = order._resolve_and_lock_bank_fee(order.company_id, area)
        currency = order.fee_currency_id
        if (
            pricing["fee_currency_id"] != currency.id
            or currency.compare_amounts(
                pricing["agreed_fee"], correction.proposed_schedule_fee
            ) != 0
            or pricing["fee_source"] != correction.pricing_source
            or (pricing["negotiated_fee_id"] or False)
            != (correction.negotiated_fee_id.id or False)
        ):
            raise ValidationError(_(
                "Corrected-location pricing changed after submission. "
                "Create a new correction proposal."
            ))
        return correction, area

    @api.private
    def _mark_decided(self, decision, actor):
        self.ensure_one()
        state = "applied" if decision == "approved" else "declined"
        super(LocationCorrectionRequest, self.sudo()).write({
            "state": state,
            "decided_by_id": actor.id,
            "decided_at": fields.Datetime.now(),
        })
        return self

    @api.private
    def _portal_snapshot(self):
        self.ensure_one()
        correction = self.sudo()
        return {
            "current_state": correction.old_state,
            "current_county": correction.old_county,
            "proposed_state": correction.proposed_state,
            "proposed_county": correction.proposed_county,
            "proposed_service_area": correction.proposed_service_area_id.display_name,
            "current_fee": correction.prior_effective_fee,
            "proposed_fee": correction.proposed_schedule_fee,
            "reason": correction.reason,
            "state": correction.state,
        }
