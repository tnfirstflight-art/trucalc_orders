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
            ("internal_request_submitted", "Internal Request Submitted"),
            ("pricing_locked", "Pricing Locked"),
            ("valuation_received", "Valuation Received"),
            ("valuation_revision_requested", "Valuation Revision Requested"),
            ("valuation_revision_submitted", "Valuation Revision Submitted"),
            ("reviewer_assigned", "Reviewer Assigned"),
            ("reviewer_reassigned", "Reviewer Reassigned"),
            ("review_accepted", "Review Accepted"),
            ("valuation_approved", "Valuation Approved"),
            ("order_completed", "Order Completed"),
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
    prior_reviewer_user_id = fields.Many2one(
        "res.users", readonly=True, ondelete="restrict",
    )
    reassignment_reason = fields.Text(readonly=True)
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
    agreed_fee = fields.Monetary(
        string="Agreed Fee", currency_field="fee_currency_id", readonly=True,
    )
    fee_source = fields.Selection(
        [("base", "Base"), ("negotiated", "Negotiated")],
        readonly=True,
    )
    service_area_id = fields.Many2one(
        "trucalc.service.area", readonly=True, ondelete="restrict",
    )
    negotiated_fee_id = fields.Many2one(
        "trucalc.negotiated.fee", readonly=True, ondelete="restrict",
    )
    fee_currency_id = fields.Many2one(
        "res.currency", readonly=True, ondelete="restrict",
    )
    fee_locked_at = fields.Datetime(readonly=True)

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
    _valuation_approval_unique = models.UniqueIndex(
        "(target_valuation_id) WHERE event_type = 'valuation_approved'",
        "A Valuation version may be approved only once.",
    )
    _order_completed_unique = models.UniqueIndex(
        "(order_id) WHERE event_type = 'order_completed'",
        "An Order may be completed only once.",
    )
    _pricing_locked_unique = models.UniqueIndex(
        "(order_id) WHERE event_type = 'pricing_locked'",
        "An Order may have only one internal pricing lock event.",
    )
    _internal_request_submitted_unique = models.UniqueIndex(
        "(order_id) WHERE event_type = 'internal_request_submitted'",
        "An Order may have only one internal submission event.",
    )

    @api.constrains(
        "event_type", "deliverable_id", "order_id", "target_valuation_id",
        "new_valuation_id", "revision_request_event_id",
        "vendor_revision_instructions", "reviewer_user_id",
        "prior_reviewer_user_id", "reassignment_reason",
        "agreed_fee", "fee_source", "service_area_id", "negotiated_fee_id",
        "fee_currency_id", "fee_locked_at",
    )
    def _check_deliverable_provenance(self):
        for event in self:
            has_fee_provenance = bool(
                event.fee_source or event.service_area_id
                or event.negotiated_fee_id or event.fee_currency_id
                or event.fee_locked_at
            )
            if event.event_type in (
                "bank_request_sent", "internal_request_submitted", "pricing_locked"
            ) and has_fee_provenance:
                if (
                    not event.fee_source or not event.service_area_id
                    or not event.fee_currency_id or not event.fee_locked_at
                    or (
                        event.fee_source == "negotiated"
                        and not event.negotiated_fee_id
                    )
                    or (
                        event.fee_source == "base"
                        and event.negotiated_fee_id
                    )
                ):
                    raise AccessError(_(
                        "Bank request fee provenance is incomplete."
                    ))
            elif has_fee_provenance:
                raise AccessError(_(
                    "This lifecycle event may not contain fee provenance."
                ))
            if event.event_type == "pricing_locked" and (
                not has_fee_provenance
                or event.from_status != event.to_status
                or event.stable_order_id != event.order_id.id
                or event.company_id != event.order_id.company_id
            ):
                raise AccessError(_("Internal pricing lock provenance is invalid."))
            if event.event_type == "internal_request_submitted" and (
                not has_fee_provenance
                or event.from_status != "draft" or event.to_status != "new"
                or event.stable_order_id != event.order_id.id
                or event.company_id != event.order_id.company_id
            ):
                raise AccessError(_("Internal submission provenance is invalid."))
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
            elif event.event_type == "valuation_approved":
                target = event.target_valuation_id
                if (
                    not target or target.artifact_type != "valuation"
                    or target.order_id != event.order_id
                    or event.new_valuation_id or event.revision_request_event_id
                    or event.vendor_revision_instructions
                    or event.prior_reviewer_user_id or event.reassignment_reason
                ):
                    raise AccessError(_("Valuation approval provenance is invalid."))
            elif event.event_type == "order_completed":
                target = event.target_valuation_id
                if (
                    not target or target.artifact_type != "valuation"
                    or target.order_id != event.order_id
                    or target.company_id != event.company_id
                    or event.stable_order_id != event.order_id.id
                    or event.company_id != event.order_id.company_id
                    or event.from_status != "under_review" or event.to_status != "completed"
                    or not event.reviewer_user_id
                    or event.new_valuation_id or event.revision_request_event_id
                    or event.vendor_revision_instructions
                    or event.prior_reviewer_user_id or event.reassignment_reason
                ):
                    raise AccessError(_("Order completion provenance is invalid."))
            elif event.event_type == "reviewer_reassigned":
                if (
                    not event.prior_reviewer_user_id or not event.reviewer_user_id
                    or event.prior_reviewer_user_id == event.reviewer_user_id
                    or not event.reassignment_reason
                    or event.target_valuation_id or event.new_valuation_id
                    or event.revision_request_event_id
                    or event.vendor_revision_instructions
                ):
                    raise AccessError(_("Reviewer reassignment provenance is invalid."))
            elif (
                event.target_valuation_id or event.new_valuation_id
                or event.revision_request_event_id
                or event.vendor_revision_instructions
                or event.prior_reviewer_user_id or event.reassignment_reason
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
        values = {
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
        }
        if event_type == "internal_request_submitted":
            values["event_at"] = order.fee_locked_at
        if event_type in (
            "bank_request_sent", "internal_request_submitted"
        ) and order.fee_locked_at:
            values.update({
                "agreed_fee": order.agreed_fee,
                "fee_source": order.fee_source,
                "service_area_id": order.service_area_id.id,
                "negotiated_fee_id": order.negotiated_fee_id.id,
                "fee_currency_id": order.fee_currency_id.id,
                "fee_locked_at": order.fee_locked_at,
            })
        return super(TruCalcOrderLifecycleEvent, self.sudo()).create(values)

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
    def _valuation_approval(self, target):
        target = target.sudo().exists()
        if len(target) != 1:
            return self.browse()
        return self.sudo().search([
            ("event_type", "=", "valuation_approved"),
            ("target_valuation_id", "=", target.id),
        ], limit=1)

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

    @api.model
    @api.private
    def _log_reviewer_reassignment(
        self, order, prior_reviewer, new_reviewer, reason,
        from_status, to_status, actor,
    ):
        return super(TruCalcOrderLifecycleEvent, self.sudo()).create({
            "order_id": order.id,
            "stable_order_id": order.id,
            "company_id": order.company_id.id,
            "event_type": "reviewer_reassigned",
            "from_status": from_status,
            "to_status": to_status,
            "actor_id": actor.id,
            "event_at": fields.Datetime.now(),
            "prior_reviewer_user_id": prior_reviewer.id,
            "reviewer_user_id": new_reviewer.id,
            "reassignment_reason": reason,
        })

    @api.model
    @api.private
    def _log_valuation_approval(self, order, target, actor):
        return super(TruCalcOrderLifecycleEvent, self.sudo()).create({
            "order_id": order.id,
            "stable_order_id": order.id,
            "company_id": order.company_id.id,
            "event_type": "valuation_approved",
            "from_status": order.status,
            "to_status": order.status,
            "actor_id": actor.id,
            "event_at": fields.Datetime.now(),
            "reviewer_user_id": actor.id,
            "target_valuation_id": target.id,
        })

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Order lifecycle events require a trusted workflow."))

    @api.model
    @api.private
    def _log_order_completion(self, order, target, actor):
        return super(TruCalcOrderLifecycleEvent, self.sudo()).create({
            "order_id": order.id, "stable_order_id": order.id,
            "company_id": order.company_id.id, "event_type": "order_completed",
            "from_status": "under_review", "to_status": "completed",
            "actor_id": actor.id, "event_at": fields.Datetime.now(),
            "reviewer_user_id": order.reviewer_user_id.id,
            "target_valuation_id": target.id,
        })

    def write(self, vals):
        raise AccessError(_("Order lifecycle events are immutable."))

    def unlink(self):
        raise AccessError(_("Order lifecycle events are immutable."))
