from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class TruCalcOrderLifecycleEvent(models.Model):
    _name = "trucalc.order.lifecycle.event"
    _description = "Immutable Order Lifecycle Event"
    _order = "event_at desc, id desc"

    order_id = fields.Many2one(
        "trucalc.order", required=True, readonly=True, index=True,
        ondelete="restrict",
    )
    stable_order_id = fields.Integer(required=True, readonly=True, index=True)
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True,
        ondelete="restrict",
    )
    event_type = fields.Selection(
        [
            ("bank_request_sent", "Bank Request Sent"),
            ("valuation_received", "Valuation Received"),
            ("reviewer_assigned", "Reviewer Assigned"),
        ],
        required=True, readonly=True, index=True,
    )
    from_status = fields.Char(required=True, readonly=True)
    to_status = fields.Char(required=True, readonly=True)
    actor_id = fields.Many2one(
        "res.users", required=True, readonly=True, index=True,
        ondelete="restrict",
    )
    event_at = fields.Datetime(required=True, readonly=True, index=True)
    reviewer_id = fields.Many2one(
        "trucalc.vendor", readonly=True, ondelete="restrict",
    )
    reviewer_user_id = fields.Many2one(
        "res.users", readonly=True, ondelete="restrict",
    )
    deliverable_id = fields.Many2one(
        "trucalc.vendor.deliverable", readonly=True, index=True,
        ondelete="restrict",
    )

    _valuation_received_unique = models.UniqueIndex(
        "(order_id) WHERE event_type = 'valuation_received'",
        "An Order may have only one initial Valuation receipt event.",
    )

    @api.constrains("event_type", "deliverable_id", "order_id")
    def _check_deliverable_provenance(self):
        for event in self:
            if event.event_type == "valuation_received" and (
                not event.deliverable_id
                or event.deliverable_id.artifact_type != "valuation"
                or event.deliverable_id.order_id != event.order_id
                or event.deliverable_id.version != 1
            ):
                raise AccessError(_("Valuation receipt event provenance is invalid."))
            if event.event_type != "valuation_received" and event.deliverable_id:
                raise AccessError(_("This lifecycle event may not reference a deliverable."))

    @api.model
    @api.private
    def _log_event(
        self, order, event_type, from_status, to_status, actor,
        deliverable=False,
    ):
        order.ensure_one()
        actor.ensure_one()
        return super(TruCalcOrderLifecycleEvent, self.sudo()).create({
            "order_id": order.id,
            "stable_order_id": order.id,
            "company_id": order.company_id.id,
            "event_type": event_type,
            "from_status": from_status,
            "to_status": to_status,
            "actor_id": actor.id,
            "event_at": fields.Datetime.now(),
            "reviewer_id": order.reviewer_id.id,
            "reviewer_user_id": order.reviewer_user_id.id,
            "deliverable_id": deliverable.id if deliverable else False,
        })

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Order lifecycle events require a trusted workflow."))

    def write(self, vals):
        raise AccessError(_("Order lifecycle events are immutable."))

    def unlink(self):
        raise AccessError(_("Order lifecycle events are immutable."))
