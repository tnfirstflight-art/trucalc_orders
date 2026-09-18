import math

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class FeeChangeRequest(models.Model):
    _name = "trucalc.fee.change.request"
    _description = "Order Scope-Change Fee Request"
    _order = "id desc"
    _rec_name = "reason"

    order_id = fields.Many2one("trucalc.order", required=True, readonly=True, index=True, ondelete="restrict")
    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True, ondelete="restrict")
    currency_id = fields.Many2one("res.currency", required=True, readonly=True, ondelete="restrict")
    requester_id = fields.Many2one("res.users", required=True, readonly=True, ondelete="restrict")
    requested_at = fields.Datetime(required=True, readonly=True)
    prior_fee = fields.Monetary(required=True, readonly=True)
    prior_approved_request_id = fields.Many2one("trucalc.fee.change.request", readonly=True, ondelete="restrict")
    proposed_fee = fields.Monetary(required=True, readonly=True)
    reason = fields.Text(required=True, readonly=True)
    state = fields.Selection([("pending", "Pending"), ("approved", "Approved"), ("declined", "Declined")], required=True, readonly=True, index=True)
    decision_actor_id = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    decision_at = fields.Datetime(readonly=True)
    decline_reason = fields.Text(readonly=True)
    location_correction_request_id = fields.Many2one(
        "trucalc.location.correction.request", readonly=True, index=True,
        ondelete="restrict",
    )

    _one_pending = models.UniqueIndex("(order_id) WHERE state = 'pending'", "An Order may have only one pending fee request.")
    _one_location_correction = models.UniqueIndex(
        "(location_correction_request_id) "
        "WHERE location_correction_request_id IS NOT NULL",
        "A staged location correction may link to only one fee request.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Fee requests require the controlled workflow."))

    def write(self, vals):
        raise AccessError(_("Fee requests are immutable outside the decision workflow."))

    def unlink(self):
        raise AccessError(_("Fee requests cannot be deleted."))

    @api.model
    @api.private
    def _reason(self, value):
        value = value.strip() if isinstance(value, str) else ""
        if not value or len(value) > 5000:
            raise ValidationError(_("A reason of 1 to 5000 characters is required."))
        return value

    @api.model
    @api.private
    def _amount(self, value, current, currency):
        try:
            if isinstance(value, bool):
                raise ValueError()
            value = float(value)
        except (ValueError, TypeError, OverflowError):
            raise ValidationError(_("Enter a valid proposed fee.")) from None
        if not math.isfinite(value) or value < 0:
            raise ValidationError(_("The proposed fee must be finite and nonnegative."))
        value = currency.round(value)
        if currency.compare_amounts(value, current) <= 0:
            raise ValidationError(_("The proposed fee must exceed the current fee at currency precision."))
        return value

    @api.model
    @api.private
    def _request(
        self, order, proposed_fee, reason, location_correction_request=False,
    ):
        actor = order._require_fee_request_actor()
        with self.env.cr.savepoint():
            order = order.sudo()
            order._lock_for_bid_lifecycle()
            order.invalidate_recordset()
            order._require_fee_request_actor()
            current = order._validate_fee_change_eligibility()
            if current["has_pending_request"]:
                raise ValidationError(_("A fee request is already pending."))
            values = {
                "order_id": order.id, "company_id": order.company_id.id,
                "currency_id": order.fee_currency_id.id,
                "requester_id": actor.id, "requested_at": fields.Datetime.now(),
                "prior_fee": current["amount"],
                "prior_approved_request_id": current["current_fee_change_request_id"],
                "proposed_fee": self._amount(proposed_fee, current["amount"], order.fee_currency_id),
                "reason": self._reason(reason), "state": "pending",
            }
            if location_correction_request:
                correction = location_correction_request.sudo().exists()
                if (
                    len(correction) != 1
                    or correction.order_id != order
                    or correction.company_id != order.company_id
                    or correction.state != "pending"
                    or correction.fee_change_request_ids
                ):
                    raise ValidationError(_(
                        "The staged location correction cannot be linked to this fee request."
                    ))
                values["location_correction_request_id"] = correction.id
            request = super(FeeChangeRequest, self.sudo()).create(values)
            order._controlled_lifecycle_write({"fee_workflow_revision": order.fee_workflow_revision + 1})
            self.env["trucalc.order.lifecycle.event"]._log_fee_change(request, "fee_change_requested")
            return request

    @api.model
    @api.private
    def _can_decide(self, actor=None):
        actor = actor or self.env.user
        return actor.active and (
            actor.has_group("trucalc_orders.group_bank_admin")
            or actor.has_group("trucalc_orders.group_bank_requestor")
        )

    @api.private
    def _authorize_bank(self, order):
        actor = self.env.user
        bank = actor._trucalc_bank_identity()
        if not self._can_decide(actor):
            raise AccessError(_(
                "Only the Order's Bank Administrator or Bank Requestor may "
                "decide a fee request."
            ))
        order.with_user(actor).check_access("read")
        request = self.sudo().exists()
        if (len(request) != 1 or request.order_id != order or order.sudo().company_id != bank
                or request.company_id != bank):
            raise AccessError(_("This fee request is not authorized."))
        return actor

    @api.private
    def _decide(self, order, decision, reason=None):
        self.ensure_one()
        actor = self._authorize_bank(order)
        if decision not in ("approved", "declined"):
            raise AccessError(_("Invalid fee decision."))
        with self.env.cr.savepoint():
            order = order.sudo()
            order._lock_for_bid_lifecycle()
            request = self.sudo()
            request.flush_recordset()
            self.env.cr.execute("SELECT id FROM trucalc_fee_change_request WHERE id = %s FOR UPDATE", (request.id,))
            request.invalidate_recordset()
            correction = request.location_correction_request_id.sudo()
            if correction:
                correction._lock()
            order.invalidate_recordset()
            self._authorize_bank(order)
            current = order._validate_fee_change_eligibility()
            if request.state != "pending":
                raise ValidationError(_("This fee request has already been decided."))
            if (request.currency_id != order.fee_currency_id
                    or request.prior_fee != current["amount"]
                    or request.prior_approved_request_id.id != current["current_fee_change_request_id"]):
                raise ValidationError(_("The fee request no longer matches the current agreement."))
            self._amount(request.proposed_fee, current["amount"], order.fee_currency_id)
            applied_area = False
            if correction:
                correction._validate_link(order, request)
                if decision == "approved":
                    correction, applied_area = correction._validate_approval(
                        order, request,
                    )
            values = {"state": decision, "decision_actor_id": actor.id, "decision_at": fields.Datetime.now()}
            if decision == "declined":
                values["decline_reason"] = self._reason(reason)
            super(FeeChangeRequest, request).write(values)
            fee_values = {"fee_workflow_revision": order.fee_workflow_revision + 1}
            if decision == "approved":
                fee_values.update(current_agreed_fee=request.proposed_fee, current_fee_change_request_id=request.id)
            order._controlled_lifecycle_write(fee_values)
            if correction and decision == "approved":
                order._controlled_location_correction_write({
                    "state": correction.proposed_state,
                    "county": correction.proposed_county,
                    "service_area_id": applied_area.id,
                    "pricing_state_id": applied_area.state_id.id,
                    "pricing_county_area_id": applied_area.id,
                })
            order._get_current_effective_fee()
            if correction:
                correction._mark_decided(decision, actor)
            self.env["trucalc.order.lifecycle.event"]._log_fee_change(request, "fee_change_" + decision)
            if correction:
                self.env[
                    "trucalc.order.lifecycle.event"
                ]._log_location_correction_decision(correction, request)
        return True

    @api.model
    @api.private
    def _portal_values(self, order):
        actor = self.env.user
        bank = actor._trucalc_bank_identity()
        order.with_user(actor).check_access("read")
        if not actor.active or order.sudo().company_id != bank:
            raise AccessError(_("Fee history is not authorized."))
        return [{
            "id": item.id, "prior_fee": item.prior_fee, "proposed_fee": item.proposed_fee,
            "reason": item.reason, "state": item.state, "requested_at": item.requested_at,
            "decision_at": item.decision_at, "decision_actor": item.decision_actor_id.name or "",
            "decline_reason": item.decline_reason or "",
            "location_correction": (
                item.location_correction_request_id._portal_snapshot()
                if item.location_correction_request_id else False
            ),
        } for item in self.sudo().search([("order_id", "=", order.id), ("company_id", "=", bank.id)])]
