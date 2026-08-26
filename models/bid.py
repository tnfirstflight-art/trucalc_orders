import math

from odoo import api, fields, models, tools, _
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.float_utils import float_compare
from markupsafe import Markup


COMMERCIAL_FIELDS = {"option_name", "bid_amount", "turn_time_days", "notes"}
RESPONSE_SELECTION = [
    ("standard_terms_accepted", "Accepted Standard Terms"),
    ("delivery_date_counter", "Accepted Fee / Changed Delivery Date"),
    ("fee_and_delivery_counter", "Countered Fee and Delivery Date"),
]


class TruCalcBid(models.Model):
    _name = "trucalc.bid"
    _description = "Vendor Bid"
    _order = "create_date desc"

    invitation_id = fields.Many2one(
        "trucalc.bid.invitation", string="Invitation", required=True,
        index=True, ondelete="restrict",
        groups=(
            "trucalc_orders.group_trucalc_admin,"
            "trucalc_orders.group_trucalc_operations,"
            "trucalc_orders.group_trucalc_reviewer,"
            "trucalc_orders.group_bank_admin,"
            "trucalc_orders.group_bank_requestor,"
            "trucalc_orders.group_bank_view_only"
        ),
    )
    order_id = fields.Many2one(
        "trucalc.order", string="Order", related="invitation_id.order_id",
        store=True, readonly=True, required=True, index=True,
        groups=(
            "trucalc_orders.group_trucalc_admin,"
            "trucalc_orders.group_trucalc_operations,"
            "trucalc_orders.group_trucalc_reviewer,"
            "trucalc_orders.group_bank_admin,"
            "trucalc_orders.group_bank_requestor,"
            "trucalc_orders.group_bank_view_only"
        ),
    )
    vendor_order_number = fields.Char(
        string="Order Number", related="invitation_id.order_id.order_number",
        store=True, readonly=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Company", related="invitation_id.company_id",
        store=True, readonly=True, index=True,
        groups=(
            "trucalc_orders.group_trucalc_admin,"
            "trucalc_orders.group_trucalc_operations,"
            "trucalc_orders.group_trucalc_reviewer,"
            "trucalc_orders.group_bank_admin,"
            "trucalc_orders.group_bank_requestor,"
            "trucalc_orders.group_bank_view_only"
        ),
    )
    vendor_id = fields.Many2one(
        "trucalc.vendor", string="Vendor", related="invitation_id.vendor_id",
        store=True, readonly=True, required=True, index=True,
        groups=(
            "trucalc_orders.group_trucalc_admin,"
            "trucalc_orders.group_trucalc_operations,"
            "trucalc_orders.group_trucalc_reviewer,"
            "trucalc_orders.group_bank_admin,"
            "trucalc_orders.group_bank_requestor,"
            "trucalc_orders.group_bank_view_only"
        ),
    )
    round_number = fields.Integer(
        string="Bidding Round", related="invitation_id.round_number",
        store=True, readonly=True, required=True, index=True,
    )
    option_name = fields.Char(string="Option Name", required=True)
    bid_amount = fields.Float(string="Bid Amount", required=True)
    turn_time_days = fields.Integer(string="Turn Time (Days)")
    notes = fields.Text(string="Notes")
    response_type = fields.Selection(
        RESPONSE_SELECTION, string="Response Type", readonly=True, copy=False,
        index=True,
    )
    proposed_delivery_date = fields.Date(
        string="Proposed Delivery Date", readonly=True, copy=False,
    )
    submitted_at = fields.Datetime(readonly=True, copy=False)
    last_revised_at = fields.Datetime(readonly=True, copy=False)
    revision_count = fields.Integer(readonly=True, copy=False, default=0)
    solicited_standard_fee = fields.Float(
        related="invitation_id.standard_fee", string="Solicited Standard Fee",
        readonly=True,
    )
    requested_delivery_date = fields.Date(
        related="invitation_id.requested_delivery_date",
        string="Requested Delivery Date", readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True,
    )
    is_currently_selectable = fields.Boolean(
        compute="_compute_is_currently_selectable", string="Currently Selectable",
    )
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
    _one_canonical_response_per_invitation = models.UniqueIndex(
        "(invitation_id) WHERE response_type IS NOT NULL",
        "An invitation may have only one canonical Vendor response.",
    )

    @api.depends(
        "status", "round_number", "vendor_id.active", "invitation_id.state",
        "order_id.status", "order_id.bidding_round",
    )
    def _compute_is_currently_selectable(self):
        for bid in self:
            bid.is_currently_selectable = bool(
                bid.status == "submitted"
                and bid.order_id.status == "bid_requested"
                and bid.round_number == bid.order_id.bidding_round
                and bid.invitation_id.state == "invited"
                and bid.vendor_id.active
            )

    @api.model
    @api.private
    def _normalized_response_values(self, invitation, response_type,
                                    proposed_fee=None, proposed_delivery_date=None,
                                    comments=None):
        labels = dict(RESPONSE_SELECTION)
        if response_type not in labels:
            raise ValidationError(_("Select a valid Vendor response."))
        requested = invitation.requested_delivery_date
        standard = invitation.standard_fee
        date = fields.Date.to_date(proposed_delivery_date) if proposed_delivery_date else False
        precision = invitation.company_id.currency_id.decimal_places
        if response_type == "standard_terms_accepted":
            fee, date, comments = standard, requested, False
        elif response_type == "delivery_date_counter":
            fee = standard
            if not date or date == requested:
                raise ValidationError(_("The proposed delivery date must differ from the requested date."))
        else:
            if proposed_fee in (None, "") or not date:
                raise ValidationError(_("A proposed fee and delivery date are required."))
            try:
                fee = float(proposed_fee)
            except (TypeError, ValueError):
                raise ValidationError(_("Enter a valid proposed fee."))
            if fee < 0:
                raise ValidationError(_("The proposed fee cannot be negative."))
            if float_compare(fee, standard, precision_digits=precision) == 0:
                raise ValidationError(_("The proposed fee must differ from the Standard Fee."))
            if date == requested:
                raise ValidationError(_("The proposed delivery date must differ from the requested date."))
        return {
            "response_type": response_type,
            "option_name": labels[response_type],
            "bid_amount": fee,
            "proposed_delivery_date": date,
            "notes": comments.strip() if isinstance(comments, str) and comments.strip() else False,
            "turn_time_days": 0,
        }

    @api.model
    @api.private
    def _response_audit_values(self, bid):
        return {
            "response_type": bid.response_type,
            "standard_fee": bid.invitation_id.standard_fee,
            "requested_delivery_date": fields.Date.to_string(
                bid.invitation_id.requested_delivery_date
            ),
            "proposed_fee": bid.bid_amount,
            "proposed_delivery_date": fields.Date.to_string(
                bid.proposed_delivery_date
            ),
            "vendor_comments": bid.notes or False,
            "status": bid.status,
        }

    @api.model
    @api.private
    def _controlled_submit_response(self, invitation, response_type, **values):
        invitation.ensure_one()
        order = invitation.order_id
        order._lock_for_bid_lifecycle()
        invitation._validate_current_active()
        existing = self.sudo().search([
            ("invitation_id", "=", invitation.id),
            ("response_type", "!=", False),
        ], limit=1)
        normalized = self._normalized_response_values(invitation, response_type, **values)
        now = fields.Datetime.now()
        def response_message(prefix, response):
            currency = invitation.company_id.currency_id
            return _(
                "%(prefix)s %(vendor)s. Response: %(response)s. "
                "Fee: %(fee)s. Delivery Date: %(date)s."
            ) % {
                "prefix": prefix,
                "vendor": invitation.vendor_id.name,
                "response": dict(RESPONSE_SELECTION)[response.response_type],
                "fee": tools.format_amount(self.env, response.bid_amount, currency),
                "date": tools.format_date(self.env, response.proposed_delivery_date),
            }
        if existing:
            existing.flush_recordset(["status", "response_type"])
            self.env.cr.execute(
                "SELECT id FROM trucalc_bid WHERE id = %s FOR UPDATE", (existing.id,)
            )
            existing.invalidate_recordset()
            invitation._validate_current_active()
            if existing.status != "submitted":
                raise ValidationError(_("This response is finalized and cannot be revised."))
            old = self._response_audit_values(existing)
            existing._controlled_write({
                **normalized,
                "last_revised_at": now,
                "revision_count": existing.revision_count + 1,
            })
            new = self._response_audit_values(existing)
            self.env["trucalc.bid.audit"]._log_event(
                "response_revised", order, invitation=invitation, bid=existing,
                old_values=old, new_values=new,
            )
            order.sudo().message_post(body=response_message(
                _("Vendor response revised by"), existing
            ))
            return existing
        if invitation.bid_ids:
            raise ValidationError(_("This invitation contains legacy Bid options and cannot accept a canonical response."))
        bid = self._controlled_create_canonical(invitation, {
            **normalized, "status": "submitted", "submitted_at": now,
        })
        self.env["trucalc.bid.audit"]._log_event(
            "response_submitted", order, invitation=invitation, bid=bid,
            new_values=self._response_audit_values(bid),
        )
        order.sudo().message_post(body=response_message(
            _("Vendor response received from"), bid
        ))
        return bid

    @api.model
    @api.private
    def _controlled_create_canonical(self, invitation, values):
        vals = dict(
            values,
            invitation_id=invitation.id,
            order_id=invitation.order_id.id,
            vendor_id=invitation.vendor_id.id,
            company_id=invitation.company_id.id,
            round_number=invitation.round_number,
        )
        return super(TruCalcBid, self.sudo()).create(vals)

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
        raise AccessError(_("Bid creation requires an explicit controlled lifecycle action."))

    def write(self, values):
        raise AccessError(_("Bid changes require an explicit controlled lifecycle action."))

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

    @api.private
    def _validate_engagement_eligibility(self):
        self.ensure_one()
        order = self.order_id
        invitation = self.invitation_id
        self._validate_structure()
        if order.company_id not in self.env.user.company_ids:
            raise AccessError(_("Vendor engagement is not authorized for this company."))
        if order.status != "bid_requested" or self.status != "submitted":
            raise ValidationError(_("The Vendor response is not eligible for engagement."))
        if (
            invitation.state != "invited"
            or invitation.is_legacy_reconstructed
            or self.round_number != order.bidding_round
            or not self.vendor_id.active
        ):
            raise ValidationError(_("The invitation is not eligible for engagement."))
        if (
            invitation.response_deadline
            and fields.Datetime.now() > invitation.response_deadline
        ):
            raise ValidationError(_("The Vendor response deadline has passed."))
        if not self.response_type or not self.proposed_delivery_date:
            raise ValidationError(_(
                "Engagement requires a canonical response with a delivery commitment."
            ))
        if self.bid_amount is False or not math.isfinite(self.bid_amount) or self.bid_amount < 0:
            raise ValidationError(_("The accepted Vendor fee is invalid."))
        if order.assigned_vendor_id or order.vendor_engaged_at:
            raise ValidationError(_("The order already contains engagement data."))
        authorization_count = self.env[
            "trucalc.order.vendor.authorization"
        ].sudo().search_count([
            ("order_id", "=", order.id),
            ("vendor_id", "=", self.vendor_id.id),
            ("company_id", "=", order.company_id.id),
            ("source", "=", "invitation"),
            ("invitation_id", "=", invitation.id),
            ("round_number", "=", order.bidding_round),
            ("active", "=", True),
        ])
        if authorization_count != 1:
            raise ValidationError(_("Active Vendor solicitation authorization is required."))
        current = self.search([
            ("order_id", "=", order.id),
            ("round_number", "=", order.bidding_round),
            ("status", "=", "selected"),
        ], limit=1)
        if current:
            raise ValidationError(_("A winning response has already been selected for this round."))
        return True

    def action_open_engagement_wizard(self):
        self._require_manager()
        self.ensure_one()
        self._validate_engagement_eligibility()
        return {
            "type": "ir.actions.act_window",
            "name": _("Engage Vendor"),
            "res_model": "trucalc.vendor.engagement.wizard",
            "view_mode": "form",
            "view_id": self.env.ref(
                "trucalc_orders.view_trucalc_vendor_engagement_wizard_form"
            ).id,
            "target": "new",
            "context": {"default_bid_id": self.id},
        }

    def action_select_bid(self):
        """Compatibility entry point: selection now always opens confirmation."""
        return self.action_open_engagement_wizard()

    @api.private
    def _action_confirm_engagement(self):
        self._require_manager()
        self.ensure_one()
        self.flush_recordset(["status", "order_id", "round_number", "invitation_id"])
        order = self.order_id
        order.flush_recordset([
            "status", "bidding_round", "assigned_vendor_id", "vendor_fee",
            "vendor_delivery_date", "vendor_engaged_at",
        ])
        self.env.cr.execute(
            "SELECT id FROM trucalc_order WHERE id = %s FOR UPDATE", (order.id,)
        )
        order.invalidate_recordset([
            "status", "bidding_round", "assigned_vendor_id", "vendor_fee",
            "vendor_delivery_date", "vendor_engaged_at",
        ])
        self.invalidate_recordset(["status", "round_number", "vendor_id", "bid_amount",
                                   "response_type", "proposed_delivery_date"])
        self.invitation_id.invalidate_recordset([
            "state", "round_number", "response_deadline", "is_legacy_reconstructed",
        ])
        self._validate_engagement_eligibility()
        others = self.search([
            ("order_id", "=", order.id),
            ("round_number", "=", order.bidding_round),
            ("status", "=", "submitted"),
            ("id", "!=", self.id),
        ])
        if others:
            others._controlled_write({"status": "not_selected"})
        self._controlled_write({"status": "selected"})
        engaged_at = fields.Datetime.now()
        old_order_values = {
            "order_status": order.status,
            "assigned_vendor_id": order.assigned_vendor_id.id or False,
            "vendor_fee": order.vendor_fee,
            "vendor_delivery_date": fields.Date.to_string(
                order.vendor_delivery_date
            ),
            "vendor_engaged_at": fields.Datetime.to_string(order.vendor_engaged_at),
        }
        order.with_context(tracking_disable=True)._controlled_lifecycle_write({
            "assigned_vendor_id": self.vendor_id.id,
            "vendor_fee": self.bid_amount,
            "vendor_delivery_date": self.proposed_delivery_date,
            "vendor_engaged_at": engaged_at,
            "status": "engaged",
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
            "vendor_engaged", order, invitation=self.invitation_id, bid=self,
            old_values={"bid_status": "submitted", **old_order_values},
            new_values={"bid_status": "selected", "order_status": "engaged",
                        "assigned_vendor_id": self.vendor_id.id,
                        "vendor_fee": self.bid_amount,
                        "vendor_delivery_date": fields.Date.to_string(
                            self.proposed_delivery_date
                        ),
                        "vendor_engaged_at": fields.Datetime.to_string(engaged_at)},
        )
        order_status_label = dict(order._fields["status"].selection)["engaged"]
        order.sudo().message_post(body=Markup(_(
            "<p><strong>Vendor engaged.</strong></p>"
            "<ul>"
            "<li>Vendor: %(vendor)s</li>"
            "<li>Agreed Fee: %(fee)s</li>"
            "<li>Vendor Delivery Date: %(delivery_date)s</li>"
            "<li>Vendor Engaged Date: %(engaged_at)s</li>"
            "<li>Engaged By: %(actor)s</li>"
            "<li>Order Status: %(order_status)s</li>"
            "</ul>"
        )) % {
            "vendor": self.vendor_id.name,
            "fee": tools.format_amount(
                self.env, self.bid_amount, order.company_id.currency_id
            ),
            "delivery_date": tools.format_date(
                self.env, self.proposed_delivery_date
            ),
            "engaged_at": tools.format_datetime(self.env, engaged_at),
            "actor": self.env.user.name,
            "order_status": order_status_label,
        })
        return True
