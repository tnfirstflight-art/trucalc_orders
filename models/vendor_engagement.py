import math

from markupsafe import Markup

from odoo import api, fields, models, tools, _
from odoo.exceptions import AccessError, ValidationError


RESPONSE_STATES = [
    ("awaiting_acceptance", "Awaiting Acceptance"),
    ("delivery_change_requested", "Delivery Change Requested"),
    ("accepted", "Accepted"),
    ("declined", "Declined"),
]

EVENT_TYPES = [
    ("vendor_accepted", "Vendor Accepted"),
    ("delivery_change_requested", "Delivery Change Requested"),
    ("delivery_change_approved", "Delivery Change Approved"),
    ("delivery_change_rejected", "Delivery Change Rejected"),
    ("vendor_declined", "Vendor Declined"),
    ("engagement_closed_reopened", "Engagement Closed — Bidding Reopened"),
]


class TruCalcVendorEngagement(models.Model):
    _name = "trucalc.vendor.engagement"
    _description = "Controlled Vendor Engagement"
    _order = "engaged_at desc, id desc"
    _rec_name = "order_id"

    _order_round_unique = models.UniqueIndex(
        "(order_id, round_number)",
        "An Order round may have only one Vendor engagement.",
    )
    _one_active_per_order = models.UniqueIndex(
        "(order_id) WHERE active IS TRUE",
        "An Order may have only one active Vendor engagement.",
    )
    _assignment_authorization_unique = models.UniqueIndex(
        "(assignment_authorization_id)",
        "An assignment authorization may anchor only one Vendor engagement.",
    )
    _source_audit_unique = models.UniqueIndex(
        "(source_engagement_audit_id)",
        "A Vendor-engaged audit may anchor only one Vendor engagement.",
    )
    _round_positive = models.Constraint(
        "CHECK(round_number > 0)", "The engagement round must be positive."
    )
    _fee_valid = models.Constraint(
        "CHECK(agreed_vendor_fee >= 0 AND agreed_vendor_fee < 'Infinity'::float8)",
        "The agreed Vendor fee must be finite and nonnegative.",
    )
    _active_closure_consistency = models.Constraint(
        "CHECK((active AND closed_at IS NULL AND closed_reason IS NULL) OR "
        "(NOT active AND closed_at IS NOT NULL AND closed_reason IS NOT NULL))",
        "Engagement closure metadata is inconsistent.",
    )

    order_id = fields.Many2one(
        "trucalc.order", required=True, readonly=True, index=True, ondelete="restrict"
    )
    vendor_id = fields.Many2one(
        "trucalc.vendor", required=True, readonly=True, index=True, ondelete="restrict"
    )
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True, ondelete="restrict"
    )
    round_number = fields.Integer(required=True, readonly=True, index=True)
    source_bid_id = fields.Many2one(
        "trucalc.bid", required=True, readonly=True, index=True, ondelete="restrict"
    )
    assignment_authorization_id = fields.Many2one(
        "trucalc.order.vendor.authorization", required=True, readonly=True,
        index=True, ondelete="restrict",
    )
    source_engagement_audit_id = fields.Many2one(
        "trucalc.bid.audit", required=True, readonly=True, index=True,
        ondelete="restrict",
    )
    response_state = fields.Selection(
        RESPONSE_STATES, required=True, readonly=True, default="awaiting_acceptance",
        index=True,
    )
    agreed_vendor_fee = fields.Float(required=True, readonly=True)
    vendor_delivery_date = fields.Date(required=True, readonly=True)
    engaged_at = fields.Datetime(required=True, readonly=True, index=True)
    engaged_by_id = fields.Many2one(
        "res.users", required=True, readonly=True, ondelete="restrict"
    )
    acceptance_mode = fields.Selection(
        [("direct_vendor", "Direct Vendor Acceptance"),
         ("delivery_change_approved", "Approved Conditional Acceptance")],
        readonly=True,
    )
    accepted_at = fields.Datetime(readonly=True, index=True)
    vendor_accepted_by_id = fields.Many2one(
        "res.users", readonly=True, ondelete="restrict",
        help="Vendor Portal actor for a direct acceptance only.",
    )
    conditional_request_event_id = fields.Many2one(
        "trucalc.vendor.engagement.event", readonly=True, ondelete="restrict",
        help="Vendor-authored conditional acceptance approved by TruCalc.",
    )
    delivery_change_approved_by_id = fields.Many2one(
        "res.users", readonly=True, ondelete="restrict",
        help="TruCalc actor who approved the Vendor's conditional response.",
    )
    pending_request_event_id = fields.Many2one(
        "trucalc.vendor.engagement.event", readonly=True, ondelete="restrict",
    )
    declined_at = fields.Datetime(readonly=True, index=True)
    declined_by_id = fields.Many2one(
        "res.users", readonly=True, ondelete="restrict"
    )
    decline_reason = fields.Text(readonly=True)
    active = fields.Boolean(required=True, readonly=True, default=True, index=True)
    closed_at = fields.Datetime(readonly=True, index=True)
    closed_reason = fields.Selection(
        [("reopened", "Bidding Reopened")], readonly=True, index=True,
    )
    event_ids = fields.One2many(
        "trucalc.vendor.engagement.event", "engagement_id", readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True,
    )

    @api.constrains(
        "response_state", "pending_request_event_id", "acceptance_mode",
        "accepted_at", "vendor_accepted_by_id", "conditional_request_event_id",
        "delivery_change_approved_by_id", "declined_at", "declined_by_id",
        "decline_reason",
    )
    def _check_response_metadata(self):
        for engagement in self:
            pending = engagement.response_state == "delivery_change_requested"
            if pending != bool(engagement.pending_request_event_id):
                raise ValidationError(_("Pending delivery-change metadata is inconsistent."))
            accepted = engagement.response_state == "accepted"
            if accepted != bool(engagement.accepted_at and engagement.acceptance_mode):
                raise ValidationError(_("Engagement acceptance metadata is inconsistent."))
            if engagement.acceptance_mode == "direct_vendor" and (
                not engagement.vendor_accepted_by_id
                or engagement.conditional_request_event_id
                or engagement.delivery_change_approved_by_id
            ):
                raise ValidationError(_("Direct Vendor acceptance metadata is inconsistent."))
            if engagement.acceptance_mode == "delivery_change_approved" and (
                engagement.vendor_accepted_by_id
                or not engagement.conditional_request_event_id
                or not engagement.delivery_change_approved_by_id
            ):
                raise ValidationError(_("Conditional acceptance metadata is inconsistent."))
            declined = engagement.response_state == "declined"
            if declined != bool(
                engagement.declined_at and engagement.declined_by_id
                and engagement.decline_reason
            ):
                raise ValidationError(_("Engagement decline metadata is inconsistent."))

    @api.constrains(
        "order_id", "vendor_id", "company_id", "round_number", "source_bid_id",
        "assignment_authorization_id", "source_engagement_audit_id",
        "agreed_vendor_fee", "vendor_delivery_date", "engaged_at", "engaged_by_id",
    )
    def _check_provenance(self):
        for engagement in self:
            order = engagement.order_id
            bid = engagement.source_bid_id
            authorization = engagement.assignment_authorization_id
            audit = engagement.source_engagement_audit_id
            if (
                order.company_id != engagement.company_id
                or order.assigned_vendor_id != engagement.vendor_id
                or order.bidding_round != engagement.round_number
                or bid.order_id != order
                or bid.vendor_id != engagement.vendor_id
                or bid.round_number != engagement.round_number
                or bid.status != "selected"
                or authorization.order_id != order
                or authorization.vendor_id != engagement.vendor_id
                or authorization.company_id != engagement.company_id
                or authorization.round_number != engagement.round_number
                or authorization.source != "assignment"
                or audit.order_id != order
                or audit.bid_id != bid
                or audit.action != "vendor_engaged"
                or audit.actor_id != engagement.engaged_by_id
                or audit.event_at != engagement.engaged_at
            ):
                raise ValidationError(_("Vendor engagement provenance is invalid."))
            if (
                not math.isfinite(engagement.agreed_vendor_fee)
                or engagement.agreed_vendor_fee < 0
            ):
                raise ValidationError(_("The agreed Vendor fee is invalid."))

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Vendor engagements require a trusted workflow."))

    def write(self, vals):
        raise AccessError(_("Vendor engagements require a trusted workflow."))

    def unlink(self):
        raise AccessError(_("Vendor engagement history cannot be deleted."))

    @api.model
    @api.private
    def _create_for_engagement(self, order, bid, authorization, audit):
        order.ensure_one()
        bid.ensure_one()
        authorization.ensure_one()
        audit.ensure_one()
        values = {
            "order_id": order.id,
            "vendor_id": bid.vendor_id.id,
            "company_id": order.company_id.id,
            "round_number": order.bidding_round,
            "source_bid_id": bid.id,
            "assignment_authorization_id": authorization.id,
            "source_engagement_audit_id": audit.id,
            "response_state": "awaiting_acceptance",
            "agreed_vendor_fee": order.vendor_fee,
            "vendor_delivery_date": order.vendor_delivery_date,
            "engaged_at": order.vendor_engaged_at,
            "engaged_by_id": audit.actor_id.id,
            "active": True,
        }
        return super(TruCalcVendorEngagement, self.sudo()).create(values)

    @api.model
    def _require_manager(self):
        if not (
            self.env.user.has_group("trucalc_orders.group_trucalc_admin")
            or self.env.user.has_group("trucalc_orders.group_trucalc_operations")
        ):
            raise AccessError(_("Only TruCalc Administrator or Operations may decide a request."))

    def _trusted_for_vendor(self):
        self.ensure_one()
        actor = self.env.user
        vendor = self.env["trucalc.bid.invitation"]._vendor_identity()
        engagement = self.sudo().browse(self.id).exists()
        if not engagement:
            raise AccessError(_("Engagement access is not authorized."))
        authorization = engagement.assignment_authorization_id
        order = engagement.order_id
        if (
            not vendor.active
            or engagement.vendor_id != vendor
            or not engagement.active
            or order.status != "engaged"
            or order.assigned_vendor_id != vendor
            or order.company_id != engagement.company_id
            or order.bidding_round != engagement.round_number
            or not authorization.active
            or authorization.source != "assignment"
            or authorization.order_id != order
            or authorization.vendor_id != vendor
            or authorization.company_id != order.company_id
            or authorization.round_number != order.bidding_round
        ):
            raise AccessError(_("Engagement access is not authorized."))
        return engagement, actor

    @api.model
    def _lock_trusted(self, engagement):
        order = engagement.order_id
        order._lock_for_bid_lifecycle()
        self.env.cr.execute(
            "SELECT id FROM trucalc_vendor_engagement WHERE id = %s FOR UPDATE",
            (engagement.id,),
        )
        engagement.invalidate_recordset()
        engagement.assignment_authorization_id.invalidate_recordset(["active"])
        return order

    def _revalidate_vendor(self, actor):
        engagement = self.with_user(actor)._trusted_for_vendor()[0]
        self._lock_trusted(engagement)
        return engagement.with_user(actor)._trusted_for_vendor()[0]

    def _response_snapshot(self, engagement):
        return {
            "response_state": engagement.response_state,
            "agreed_vendor_fee": engagement.agreed_vendor_fee,
            "authoritative_delivery_date": fields.Date.to_string(
                engagement.vendor_delivery_date
            ),
        }

    def action_vendor_accept(self):
        engagement, actor = self._trusted_for_vendor()
        engagement = self._revalidate_vendor(actor)
        if engagement.response_state != "awaiting_acceptance":
            raise ValidationError(_("This engagement is not awaiting acceptance."))
        now = fields.Datetime.now()
        old = self._response_snapshot(engagement)
        engagement._controlled_write({
            "response_state": "accepted",
            "acceptance_mode": "direct_vendor",
            "accepted_at": now,
            "vendor_accepted_by_id": actor.id,
        })
        event = self.env["trucalc.vendor.engagement.event"]._log_event(
            engagement, "vendor_accepted", actor=actor,
            old_state=old["response_state"], new_state="accepted",
            agreed_vendor_fee=engagement.agreed_vendor_fee,
            authoritative_delivery_date=engagement.vendor_delivery_date,
        )
        engagement.order_id.sudo().message_post(body=Markup(_(
            "<p><strong>Vendor accepted the engagement.</strong></p><ul>"
            "<li>Vendor: %(vendor)s</li><li>Accepted By: %(actor)s</li>"
            "<li>Agreed Fee: %(fee)s</li>"
            "<li>Vendor Delivery Date: %(date)s</li></ul>"
        )) % {
            "vendor": engagement.vendor_id.name,
            "actor": actor.name,
            "fee": tools.format_amount(
                self.env, engagement.agreed_vendor_fee, engagement.currency_id
            ),
            "date": tools.format_date(self.env, engagement.vendor_delivery_date),
        })
        return event

    def action_vendor_request_delivery_change(self, requested_date, reason):
        engagement, actor = self._trusted_for_vendor()
        engagement = self._revalidate_vendor(actor)
        if engagement.response_state != "awaiting_acceptance":
            raise ValidationError(_("A delivery change cannot be requested in this state."))
        try:
            requested = fields.Date.to_date(requested_date)
        except (TypeError, ValueError):
            requested = False
        reason = reason.strip() if isinstance(reason, str) else False
        today = fields.Date.context_today(self)
        if not requested or not reason:
            raise ValidationError(_("Requested Delivery Date and Reason / Comments are required."))
        if requested <= engagement.vendor_delivery_date or requested <= today:
            raise ValidationError(_(
                "Requested Delivery Date must be later than both the current Vendor "
                "Delivery Date and the current date."
            ))
        event = self.env["trucalc.vendor.engagement.event"]._log_event(
            engagement, "delivery_change_requested", actor=actor,
            old_state="awaiting_acceptance", new_state="delivery_change_requested",
            agreed_vendor_fee=engagement.agreed_vendor_fee,
            authoritative_delivery_date=engagement.vendor_delivery_date,
            requested_delivery_date=requested, reason=reason,
        )
        engagement._controlled_write({
            "response_state": "delivery_change_requested",
            "pending_request_event_id": event.id,
        })
        engagement.order_id.sudo().message_post(body=Markup(_(
            "<p><strong>Vendor requested a delivery-date change.</strong></p><ul>"
            "<li>Vendor: %(vendor)s</li><li>Requested By: %(actor)s</li>"
            "<li>Current Vendor Delivery Date: %(current)s</li>"
            "<li>Requested Vendor Delivery Date: %(requested)s</li>"
            "<li>Reason / Comments: %(reason)s</li>"
            "<li>Agreed Fee (unchanged): %(fee)s</li></ul>"
        )) % {
            "vendor": engagement.vendor_id.name,
            "actor": actor.name,
            "current": tools.format_date(self.env, engagement.vendor_delivery_date),
            "requested": tools.format_date(self.env, requested),
            "reason": reason,
            "fee": tools.format_amount(
                self.env, engagement.agreed_vendor_fee, engagement.currency_id
            ),
        })
        return event

    def action_vendor_decline(self, reason):
        engagement, actor = self._trusted_for_vendor()
        engagement = self._revalidate_vendor(actor)
        if engagement.response_state != "awaiting_acceptance":
            raise ValidationError(_("This engagement cannot be declined in its current state."))
        reason = reason.strip() if isinstance(reason, str) else False
        if not reason:
            raise ValidationError(_("Decline Reason is required."))
        now = fields.Datetime.now()
        engagement._controlled_write({
            "response_state": "declined", "declined_at": now,
            "declined_by_id": actor.id, "decline_reason": reason,
        })
        event = self.env["trucalc.vendor.engagement.event"]._log_event(
            engagement, "vendor_declined", actor=actor,
            old_state="awaiting_acceptance", new_state="declined",
            agreed_vendor_fee=engagement.agreed_vendor_fee,
            authoritative_delivery_date=engagement.vendor_delivery_date,
            reason=reason,
        )
        engagement.order_id.sudo().message_post(body=Markup(_(
            "<p><strong>Vendor declined the engagement.</strong></p><ul>"
            "<li>Vendor: %(vendor)s</li><li>Declined By: %(actor)s</li>"
            "<li>Reason: %(reason)s</li><li>Agreed Fee Declined: %(fee)s</li>"
            "<li>Vendor Delivery Date Declined: %(date)s</li></ul>"
        )) % {
            "vendor": engagement.vendor_id.name, "actor": actor.name,
            "reason": reason,
            "fee": tools.format_amount(
                self.env, engagement.agreed_vendor_fee, engagement.currency_id
            ),
            "date": tools.format_date(self.env, engagement.vendor_delivery_date),
        })
        return event

    def _trusted_for_manager(self):
        self.ensure_one()
        self._require_manager()
        engagement = self.sudo().browse(self.id).exists()
        if not engagement or engagement.company_id not in self.env.user.company_ids:
            raise AccessError(_("Engagement decision access is not authorized."))
        self._lock_trusted(engagement)
        engagement.invalidate_recordset()
        if (
            not engagement.active
            or engagement.order_id.status != "engaged"
            or engagement.order_id.assigned_vendor_id != engagement.vendor_id
            or not engagement.assignment_authorization_id.active
            or engagement.response_state != "delivery_change_requested"
            or not engagement.pending_request_event_id
        ):
            raise ValidationError(_("There is no current delivery-change request to decide."))
        return engagement

    def action_approve_delivery_change(self, comment=None):
        engagement = self._trusted_for_manager()
        actor = self.env.user
        request_event = engagement.pending_request_event_id
        requested = request_event.requested_delivery_date
        old_date = engagement.vendor_delivery_date
        now = fields.Datetime.now()
        engagement.order_id.with_context(
            tracking_disable=True
        )._controlled_lifecycle_write({"vendor_delivery_date": requested})
        engagement._controlled_write({
            "response_state": "accepted",
            "vendor_delivery_date": requested,
            "acceptance_mode": "delivery_change_approved",
            "accepted_at": now,
            "conditional_request_event_id": request_event.id,
            "delivery_change_approved_by_id": actor.id,
            "pending_request_event_id": False,
        })
        comment = comment.strip() if isinstance(comment, str) and comment.strip() else False
        event = self.env["trucalc.vendor.engagement.event"]._log_event(
            engagement, "delivery_change_approved", actor=actor,
            prior_event=request_event, old_state="delivery_change_requested",
            new_state="accepted", agreed_vendor_fee=engagement.agreed_vendor_fee,
            authoritative_delivery_date=requested,
            requested_delivery_date=requested, reason=comment,
        )
        engagement.order_id.sudo().message_post(body=Markup(_(
            "<p><strong>Vendor delivery-date change approved; engagement accepted.</strong></p>"
            "<ul><li>Vendor: %(vendor)s</li>"
            "<li>Conditional Response By: %(vendor_actor)s</li>"
            "<li>Approved By: %(approver)s</li>"
            "<li>Prior Vendor Delivery Date: %(old_date)s</li>"
            "<li>Approved Vendor Delivery Date: %(new_date)s</li>"
            "<li>Agreed Fee (unchanged): %(fee)s</li></ul>"
        )) % {
            "vendor": engagement.vendor_id.name,
            "vendor_actor": request_event.actor_id.name,
            "approver": actor.name,
            "old_date": tools.format_date(self.env, old_date),
            "new_date": tools.format_date(self.env, requested),
            "fee": tools.format_amount(
                self.env, engagement.agreed_vendor_fee, engagement.currency_id
            ),
        })
        return event

    def action_reject_delivery_change(self, reason):
        engagement = self._trusted_for_manager()
        actor = self.env.user
        reason = reason.strip() if isinstance(reason, str) else False
        if not reason:
            raise ValidationError(_("Rejection Reason is required."))
        request_event = engagement.pending_request_event_id
        engagement._controlled_write({
            "response_state": "awaiting_acceptance",
            "pending_request_event_id": False,
        })
        event = self.env["trucalc.vendor.engagement.event"]._log_event(
            engagement, "delivery_change_rejected", actor=actor,
            prior_event=request_event, old_state="delivery_change_requested",
            new_state="awaiting_acceptance",
            agreed_vendor_fee=engagement.agreed_vendor_fee,
            authoritative_delivery_date=engagement.vendor_delivery_date,
            requested_delivery_date=request_event.requested_delivery_date,
            reason=reason,
        )
        engagement.order_id.sudo().message_post(body=Markup(_(
            "<p><strong>Vendor delivery-date change rejected.</strong></p><ul>"
            "<li>Vendor: %(vendor)s</li><li>Requested By: %(vendor_actor)s</li>"
            "<li>Rejected By: %(reviewer)s</li>"
            "<li>Requested Vendor Delivery Date: %(requested)s</li>"
            "<li>Authoritative Vendor Delivery Date (unchanged): %(current)s</li>"
            "<li>Rejection Reason: %(reason)s</li></ul>"
        )) % {
            "vendor": engagement.vendor_id.name,
            "vendor_actor": request_event.actor_id.name,
            "reviewer": actor.name,
            "requested": tools.format_date(
                self.env, request_event.requested_delivery_date
            ),
            "current": tools.format_date(self.env, engagement.vendor_delivery_date),
            "reason": reason,
        })
        return event

    @api.private
    def _controlled_write(self, values):
        return super(TruCalcVendorEngagement, self.sudo()).write(values)

    @api.private
    def _close_for_reopen(self):
        self.ensure_one()
        if not self.active:
            return False
        actor = self.env.user
        now = fields.Datetime.now()
        self.env["trucalc.vendor.engagement.event"]._log_event(
            self, "engagement_closed_reopened", actor=actor,
            old_state=self.response_state, new_state=self.response_state,
            agreed_vendor_fee=self.agreed_vendor_fee,
            authoritative_delivery_date=self.vendor_delivery_date,
            requested_delivery_date=(
                self.pending_request_event_id.requested_delivery_date
                if self.pending_request_event_id else False
            ),
        )
        self._controlled_write({
            "active": False, "closed_at": now, "closed_reason": "reopened",
        })
        return True


class TruCalcVendorEngagementEvent(models.Model):
    _name = "trucalc.vendor.engagement.event"
    _description = "Immutable Vendor Engagement Event"
    _order = "event_at desc, id desc"

    engagement_id = fields.Many2one(
        "trucalc.vendor.engagement", required=True, readonly=True, index=True,
        ondelete="restrict",
    )
    event_type = fields.Selection(
        EVENT_TYPES, required=True, readonly=True, index=True,
    )
    actor_id = fields.Many2one(
        "res.users", required=True, readonly=True, index=True, ondelete="restrict"
    )
    event_at = fields.Datetime(required=True, readonly=True, index=True)
    prior_event_id = fields.Many2one(
        "trucalc.vendor.engagement.event", readonly=True, index=True,
        ondelete="restrict",
    )
    old_response_state = fields.Selection(RESPONSE_STATES, readonly=True)
    new_response_state = fields.Selection(RESPONSE_STATES, readonly=True)
    agreed_vendor_fee = fields.Float(required=True, readonly=True)
    authoritative_delivery_date = fields.Date(required=True, readonly=True)
    requested_delivery_date = fields.Date(readonly=True)
    reason = fields.Text(readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True, ondelete="restrict"
    )

    _fee_valid = models.Constraint(
        "CHECK(agreed_vendor_fee >= 0 AND agreed_vendor_fee < 'Infinity'::float8)",
        "The event fee snapshot must be finite and nonnegative.",
    )

    @api.constrains("engagement_id", "company_id", "prior_event_id")
    def _check_provenance(self):
        for event in self:
            if event.company_id != event.engagement_id.company_id:
                raise ValidationError(_("Engagement event company provenance is invalid."))
            if event.prior_event_id and (
                event.prior_event_id.engagement_id != event.engagement_id
                or event.prior_event_id.event_type != "delivery_change_requested"
            ):
                raise ValidationError(_("Engagement decision linkage is invalid."))

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Engagement events require a trusted workflow."))

    def write(self, vals):
        raise AccessError(_("Engagement events are immutable."))

    def unlink(self):
        raise AccessError(_("Engagement events are immutable."))

    @api.model
    @api.private
    def _log_event(
        self, engagement, event_type, actor, old_state, new_state,
        agreed_vendor_fee, authoritative_delivery_date,
        prior_event=False, requested_delivery_date=False, reason=False,
    ):
        values = {
            "engagement_id": engagement.id,
            "event_type": event_type,
            "actor_id": actor.id,
            "event_at": fields.Datetime.now(),
            "prior_event_id": prior_event.id if prior_event else False,
            "old_response_state": old_state,
            "new_response_state": new_state,
            "agreed_vendor_fee": agreed_vendor_fee,
            "authoritative_delivery_date": authoritative_delivery_date,
            "requested_delivery_date": requested_delivery_date,
            "reason": reason,
            "company_id": engagement.company_id.id,
        }
        return super(TruCalcVendorEngagementEvent, self.sudo()).create(values)
