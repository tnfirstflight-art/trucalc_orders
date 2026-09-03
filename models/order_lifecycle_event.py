from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


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
            ("valuation_revision_requested", "Valuation Revision Requested"),
            ("valuation_revision_submitted", "Valuation Revision Submitted"),
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
    target_valuation_id = fields.Many2one(
        "trucalc.vendor.deliverable", readonly=True, index=True,
        ondelete="restrict",
    )
    new_valuation_id = fields.Many2one(
        "trucalc.vendor.deliverable", readonly=True, index=True,
        ondelete="restrict",
    )
    revision_request_event_id = fields.Many2one(
        "trucalc.order.lifecycle.event", readonly=True, index=True,
        ondelete="restrict",
    )
    vendor_revision_instructions = fields.Text(readonly=True)

    _valuation_received_unique = models.UniqueIndex(
        "(order_id) WHERE event_type = 'valuation_received'",
        "An Order may have only one initial Valuation receipt event.",
    )
    _valuation_revision_request_unique = models.UniqueIndex(
        "(target_valuation_id) WHERE event_type = 'valuation_revision_requested'",
        "A Valuation version may receive only one revision request.",
    )
    _valuation_revision_submission_unique = models.UniqueIndex(
        "(revision_request_event_id) "
        "WHERE event_type = 'valuation_revision_submitted'",
        "A Valuation revision request may be consumed only once.",
    )

    @api.constrains(
        "event_type", "deliverable_id", "order_id", "target_valuation_id",
        "new_valuation_id", "revision_request_event_id",
        "vendor_revision_instructions",
    )
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
            if event.event_type == "valuation_revision_requested":
                target = event.target_valuation_id
                if (
                    not target or target.artifact_type != "valuation"
                    or target.order_id != event.order_id
                    or not event.vendor_revision_instructions
                    or event.new_valuation_id or event.revision_request_event_id
                ):
                    raise AccessError(_("Valuation revision request provenance is invalid."))
            elif event.event_type == "valuation_revision_submitted":
                request = event.revision_request_event_id
                prior = event.target_valuation_id
                new = event.new_valuation_id
                if (
                    not request
                    or request.event_type != "valuation_revision_requested"
                    or request.order_id != event.order_id
                    or request.target_valuation_id != prior
                    or not prior or prior.artifact_type != "valuation"
                    or prior.order_id != event.order_id
                    or not new or new.artifact_type != "valuation"
                    or new.order_id != event.order_id
                    or new.version != prior.version + 1
                    or event.vendor_revision_instructions
                ):
                    raise AccessError(_("Revised Valuation submission provenance is invalid."))
            elif (
                event.target_valuation_id or event.new_valuation_id
                or event.revision_request_event_id
                or event.vendor_revision_instructions
            ):
                raise AccessError(_("This lifecycle event contains invalid revision data."))

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

    @api.model
    @api.private
    def _open_valuation_revision_request(self, target):
        target = target.sudo().exists()
        if len(target) != 1:
            return self.browse()
        request = self.sudo().search([
            ("event_type", "=", "valuation_revision_requested"),
            ("target_valuation_id", "=", target.id),
        ], limit=1)
        if not request:
            return request
        consumed = self.sudo().search_count([
            ("event_type", "=", "valuation_revision_submitted"),
            ("revision_request_event_id", "=", request.id),
        ])
        return self.browse() if consumed else request

    @api.model
    @api.private
    def _request_valuation_revision(self, order, target, instructions, actor):
        order = order.sudo().exists()
        target = target.sudo().exists()
        actor = actor.sudo().exists()
        if (
            len(order) != 1 or len(target) != 1 or len(actor) != 1
            or actor != self.env.user or not actor.active or actor.share
        ):
            raise AccessError(_("Valuation revision authorization is not permitted."))
        instructions = instructions.strip() if isinstance(instructions, str) else ""
        if not instructions:
            raise ValidationError(_("Vendor-facing revision instructions are required."))
        if len(instructions) > 5000:
            raise ValidationError(_("Vendor-facing revision instructions may not exceed 5000 characters."))
        order._lock_for_bid_lifecycle()
        self.env.cr.execute(
            "SELECT id FROM trucalc_vendor_deliverable WHERE id = %s FOR UPDATE",
            (target.id,),
        )
        target.invalidate_recordset()
        order.invalidate_recordset()
        if (
            target.order_id != order or target.artifact_type != "valuation"
            or target.status != "submitted" or not target.is_current
            or order.status not in ("report_received", "reviewer_assigned", "under_review")
        ):
            raise ValidationError(_("The targeted Valuation is no longer eligible for revision."))
        if self.sudo().search_count([
            ("event_type", "=", "valuation_revision_requested"),
            ("target_valuation_id", "=", target.id),
        ]):
            raise ValidationError(_("This Valuation already has a revision request."))
        return super(TruCalcOrderLifecycleEvent, self.sudo()).create({
            "order_id": order.id,
            "stable_order_id": order.id,
            "company_id": order.company_id.id,
            "event_type": "valuation_revision_requested",
            "from_status": order.status,
            "to_status": order.status,
            "actor_id": actor.id,
            "event_at": fields.Datetime.now(),
            "target_valuation_id": target.id,
            "vendor_revision_instructions": instructions,
        })

    @api.model
    @api.private
    def _log_valuation_revision_submission(
        self, order, request, prior, new, actor,
    ):
        return super(TruCalcOrderLifecycleEvent, self.sudo()).create({
            "order_id": order.id,
            "stable_order_id": order.id,
            "company_id": order.company_id.id,
            "event_type": "valuation_revision_submitted",
            "from_status": order.status,
            "to_status": order.status,
            "actor_id": actor.id,
            "event_at": fields.Datetime.now(),
            "target_valuation_id": prior.id,
            "new_valuation_id": new.id,
            "revision_request_event_id": request.id,
        })

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Order lifecycle events require a trusted workflow."))

    def write(self, vals):
        raise AccessError(_("Order lifecycle events are immutable."))

    def unlink(self):
        raise AccessError(_("Order lifecycle events are immutable."))
