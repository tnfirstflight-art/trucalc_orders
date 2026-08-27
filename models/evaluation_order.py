from odoo import api, fields, models, tools, _
from odoo.exceptions import AccessError, ValidationError
from markupsafe import Markup, escape

from .vendor_fee import SERVICE_SELECTION



class EvaluationOrder(models.Model):
    _name = "trucalc.order"
    _description = "TruCalc Order"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    order_number = fields.Char(
        string="Order Number",
        required=True,
        copy=False,
        readonly=True,
        default="New",
    )

    borrower = fields.Char(
        string="Borrower",
        required=True,
        tracking=True,
    )

    property_address = fields.Char(
        string="Property Address",
        required=True,
        tracking=True,
    )

    city = fields.Char(
        string="City",
    )

    state = fields.Char(
        string="State",
    )

    zip_code = fields.Char(
        string="ZIP",
    )

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True,
    )

    requestor_company_id = fields.Many2one(
        "res.company",
        string="Requestor Company",
        default=lambda self: self.env.company,
        readonly=True,
    )

    requestor_id = fields.Many2one(
        "res.users",
        string="Requestor",
        default=lambda self: self.env.user,
        readonly=True,
        tracking=True,
    )

    loan_number = fields.Char(
        string="Loan Number",
        tracking=True,
    )

    service_type = fields.Selection(
        SERVICE_SELECTION,
        string="Service Type",
        tracking=True,
    )

    property_type = fields.Selection(
        [
            ("single_family", "Single Family"),
            ("duplex", "Duplex"),
            ("triplex", "Triplex"),
            ("quadplex", "Fourplex"),
            ("condo", "Condominium"),
            ("land", "Land"),
            ("commercial", "Commercial"),
        ],
        string="Property Type",
        tracking=True,
    )

    loan_amount = fields.Float(
        string="Loan Amount",
    )

    assigned_vendor_id = fields.Many2one(
        "trucalc.vendor",
        string="Assigned Vendor",
        tracking=True,
    )

    vendor_fee = fields.Float(
        string="Vendor Fee",
        tracking=True,
    )

    bidding_round = fields.Integer(
        string="Bidding Round",
        default=0,
    )

    order_date = fields.Date(
        string="Order Date",
        readonly=True,
        copy=False,
    )

    order_date_display = fields.Char(
        string="Order Date Display",
        compute="_compute_order_date_display",
        readonly=True,
    )

    due_date = fields.Date(
        string="Client Due Date",
        tracking=True,
    )

    vendor_delivery_date = fields.Date(
        string="Vendor Delivery Date",
        readonly=True,
        copy=False,
        tracking=True,
    )

    vendor_engaged_at = fields.Datetime(
        string="Vendor Engaged Date",
        readonly=True,
        copy=False,
        tracking=True,
        help="Date and time an authorized TruCalc user confirmed Vendor engagement.",
    )

    engagement_ids = fields.One2many(
        "trucalc.vendor.engagement", "order_id", string="Vendor Engagements",
        readonly=True,
    )
    current_engagement_id = fields.Many2one(
        "trucalc.vendor.engagement", compute="_compute_current_engagement",
        compute_sudo=True, readonly=True, search="_search_current_engagement",
    )
    engagement_response_state = fields.Selection(
        related="current_engagement_id.response_state", readonly=True,
        string="Engagement Response",
    )
    engagement_requested_delivery_date = fields.Date(
        related="current_engagement_id.pending_request_event_id.requested_delivery_date",
        readonly=True, string="Requested Delivery Date",
    )
    engagement_request_reason = fields.Text(
        related="current_engagement_id.pending_request_event_id.reason",
        readonly=True, string="Delivery Change Reason / Comments",
    )
    engagement_decline_reason = fields.Text(
        related="current_engagement_id.decline_reason", readonly=True,
        string="Engagement Decline Reason",
    )
    engagement_action_required = fields.Boolean(
        compute="_compute_engagement_action_required",
        search="_search_engagement_action_required",
        compute_sudo=True,
        readonly=True,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations",
    )
    engagement_action_required_label = fields.Char(
        compute="_compute_engagement_action_required",
        compute_sudo=True,
        readonly=True,
        string="Operational Attention",
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations",
    )
    engagement_action_required_reason = fields.Selection(
        [
            ("delivery_change_requested", "Delivery Change Requested"),
            ("declined", "Vendor Declined"),
        ],
        compute="_compute_engagement_action_required",
        compute_sudo=True,
        readonly=True,
        string="Reason",
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations",
    )
    current_round_has_solicitation = fields.Boolean(
        compute="_compute_current_round_solicitation_controls",
        compute_sudo=True,
        readonly=True,
    )
    can_request_vendor_bids = fields.Boolean(
        compute="_compute_current_round_solicitation_controls",
        compute_sudo=True,
        readonly=True,
    )

    status = fields.Selection(
        [
            ("new", "New"),
            ("accepted", "Accepted"),
            ("bid_requested", "Bid Requested"),
            ("assigned", "Assigned"),
            ("engaged", "Engaged"),
            ("report_received", "Report Received"),
            ("reviewer_assigned", "Reviewer Assigned"),
            ("under_review", "Under Review"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
            ("declined", "Declined"),
        ],
        string="Status",
        default="new",
        tracking=True,
    )

    @api.depends("engagement_ids.active")
    def _compute_current_engagement(self):
        for order in self:
            order.current_engagement_id = order.engagement_ids.filtered("active")[:1]

    def _search_current_engagement(self, operator, value):
        return [
            ("engagement_ids.active", "=", True),
            ("engagement_ids", operator, value),
        ]

    @api.depends("current_engagement_id.response_state")
    def _compute_engagement_action_required(self):
        reasons = {
            "delivery_change_requested": "delivery_change_requested",
            "declined": "declined",
        }
        for order in self:
            reason = reasons.get(order.current_engagement_id.response_state)
            order.engagement_action_required = bool(reason)
            order.engagement_action_required_label = (
                _("Action Required") if reason else False
            )
            order.engagement_action_required_reason = reason

    def _search_engagement_action_required(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise ValidationError(_("Action Required supports only Boolean searches."))
        qualifying = self.env["trucalc.vendor.engagement"].sudo().search([
            ("active", "=", True),
            ("response_state", "in", ("delivery_change_requested", "declined")),
            ("company_id", "in", self.env.companies.ids),
        ]).mapped("order_id").ids
        wants_qualifying = (operator == "=" and value) or (
            operator == "!=" and not value
        )
        return [("id", "in" if wants_qualifying else "not in", qualifying)]

    @api.depends(
        "status", "bidding_round", "invitation_ids.round_number",
        "bid_ids.round_number", "vendor_authorization_ids.round_number",
        "vendor_authorization_ids.source",
    )
    def _compute_current_round_solicitation_controls(self):
        for order in self:
            invitations, authorizations, bids = order._current_round_solicitation_facts()
            order.current_round_has_solicitation = bool(invitations)
            order.can_request_vendor_bids = bool(
                order.id
                and (
                    (order.status == "accepted" and order.bidding_round == 0)
                    or (
                        order.status == "bid_requested"
                        and order.bidding_round > 0
                        and not invitations
                        and not authorizations
                        and not bids
                    )
                )
            )

    notes = fields.Text(
        string="Notes",
    )

    decline_reason = fields.Text(
        string="Reason for Decline",
        readonly=True,
        copy=False,
    )

    reviewer_id = fields.Many2one(
        "trucalc.vendor",
        string="Reviewer",
        tracking=True,
        domain="[('active', '=', True), ('fee_schedule_ids.service_type', '=', 'review')]",
    )

    review_fee = fields.Float(
        string="Review Fee",
        tracking=True,
    )

    fee_override = fields.Boolean(
        string="Fee Override",
        default=False,
        tracking=True,
    )

    document_ids = fields.One2many(
        "trucalc.document",
        "order_id",
        string="Documents",
    )

    invitation_ids = fields.One2many(
        "trucalc.bid.invitation",
        "order_id",
        string="Bid Invitations",
    )

    bid_ids = fields.One2many(
        "trucalc.bid",
        "order_id",
        string="Bids",
    )

    vendor_authorization_ids = fields.One2many(
        "trucalc.order.vendor.authorization", "order_id",
        string="Vendor Order Authorizations", readonly=True, copy=False,
    )

    @api.depends("order_date")
    def _compute_order_date_display(self):
        for order in self:
            order_date = fields.Date.to_date(order.order_date)
            if not order_date and not order._origin:
                order_date = fields.Date.context_today(order)
            order.order_date_display = (
                order_date.strftime("%m/%d/%Y") if order_date else False
            )

    @api.model_create_multi
    def create(self, vals_list):
        user = self.env.user
        bank_company = False
        if user._trucalc_has_bank_role():
            bank_company = user._trucalc_bank_identity()
        authoritative_order_date = fields.Date.context_today(self)
        for vals in vals_list:
            if "vendor_authorization_ids" in vals:
                raise AccessError(_("Vendor Order authorization is server-maintained."))
            if "order_date" in vals:
                raise AccessError(_("Order Date is system-controlled."))
            if not vals.get("due_date"):
                raise ValidationError(_("Client Due Date is required."))
            vals["order_date"] = authoritative_order_date
            if (
                vals.get("status", "new") != "new"
                or vals.get("bidding_round", 0) != 0
                or vals.get("assigned_vendor_id")
                or vals.get("vendor_fee")
                or vals.get("vendor_delivery_date")
                or vals.get("vendor_engaged_at")
            ):
                raise AccessError(_("Bid lifecycle fields cannot be set during order creation."))
            if bank_company:
                trusted = {
                    "company_id": bank_company.id,
                    "requestor_company_id": bank_company.id,
                    "requestor_id": user.id,
                }
                if any(
                    field in vals and vals[field] != value
                    for field, value in trusted.items()
                ):
                    raise AccessError(_("TruCalc bank order ownership is not authorized."))
                vals.update(trusted)
            if vals.get("order_number", "New") == "New":
                sequence = self.env["ir.sequence"]
                if bank_company:
                    sequence = self.env.ref(
                        "trucalc_orders.seq_trucalc_order"
                    ).sudo()
                vals["order_number"] = (
                    sequence.next_by_id()
                    if bank_company
                    else sequence.next_by_code("trucalc.order")
                )
                vals["order_number"] = vals["order_number"] or "New"

        if not bank_company:
            orders = super().create(vals_list)
        else:
            trusted_context = dict(self.env.context)
            trusted_context["allowed_company_ids"] = []
            for field in ("company_id", "requestor_company_id", "requestor_id"):
                trusted_context.pop("default_%s" % field, None)
            trusted_model = self.with_context(trusted_context)
            orders = super(EvaluationOrder, trusted_model).create(vals_list)
        for order in orders:
            order.sudo().message_post(body=Markup(_(
                "<p><strong>Order Created</strong></p>"
                "<ul>"
                "<li>Order Date: %(order_date)s</li>"
                "<li>Created At: %(created_at)s</li>"
                "</ul>"
            )) % {
                "order_date": order.order_date.strftime("%m/%d/%Y"),
                "created_at": tools.format_datetime(
                    order.env, order.create_date, tz=user.tz, dt_format="medium"
                ),
            })
        return orders

    def write(self, vals):
        if "vendor_authorization_ids" in vals:
            raise AccessError(_("Vendor Order authorization is server-maintained."))
        if "decline_reason" in vals:
            raise AccessError(_("The decline reason requires the controlled decline action."))
        if "order_date" in vals:
            raise AccessError(_("Order Date is system-controlled."))
        if "due_date" in vals and not vals["due_date"]:
            raise ValidationError(_("Client Due Date is required."))
        if self.env.user._trucalc_has_bank_role():
            self.env.user._trucalc_bank_identity()
            if {"company_id", "requestor_company_id", "requestor_id"} & vals.keys():
                raise AccessError(_("TruCalc bank order ownership is immutable."))
        protected = {
            "bidding_round", "assigned_vendor_id", "vendor_fee",
            "vendor_delivery_date", "vendor_engaged_at",
        }
        if protected.intersection(vals):
            raise AccessError(_("Order bid lifecycle fields require an explicit action."))
        if "status" in vals:
            protected_transitions = {
                ("new", "bid_requested"),
                ("new", "accepted"),
                ("new", "declined"),
                ("accepted", "bid_requested"),
                ("assigned", "bid_requested"),
                ("bid_requested", "assigned"),
                ("bid_requested", "engaged"),
                ("engaged", "bid_requested"),
            }
            if any(
                order.status in ("new", "declined")
                or (order.status, vals["status"]) in protected_transitions
                for order in self
            ):
                raise AccessError(_("This order status transition requires an explicit action."))
        result = super().write(vals)
        if vals.get("status") in ("completed", "cancelled"):
            self.env["trucalc.order.vendor.authorization"]._deactivate(
                [("order_id", "in", self.ids)], vals["status"]
            )
        return result

    @api.private
    def _controlled_lifecycle_write(self, vals):
        return super(EvaluationOrder, self).write(vals)

    @api.model
    @api.private
    def _require_bid_manager(self):
        if not (
            self.env.user.has_group("trucalc_orders.group_trucalc_admin")
            or self.env.user.has_group("trucalc_orders.group_trucalc_operations")
        ):
            raise AccessError(_("Only TruCalc bid managers may perform this operation."))

    @api.model
    @api.private
    def _require_intake_manager(self):
        if not (
            self.env.user.has_group("trucalc_orders.group_trucalc_admin")
            or self.env.user.has_group("trucalc_orders.group_trucalc_operations")
        ):
            raise AccessError(_("Only TruCalc intake managers may perform this operation."))

    @api.private
    def _lock_for_bid_lifecycle(self):
        self.ensure_one()
        self.flush_recordset(
            ["status", "bidding_round", "assigned_vendor_id", "vendor_fee",
             "vendor_delivery_date", "vendor_engaged_at"]
        )
        self.env.cr.execute(
            "SELECT id FROM trucalc_order WHERE id = %s FOR UPDATE", (self.id,)
        )
        self.invalidate_recordset(
            ["status", "bidding_round", "assigned_vendor_id", "vendor_fee",
             "vendor_delivery_date", "vendor_engaged_at"]
        )

    @api.private
    def _validate_new_intake_disposition(self):
        self.ensure_one()
        if (
            self.status != "new"
            or self.bidding_round != 0
            or self.invitation_ids
            or self.bid_ids
            or self.assigned_vendor_id
        ):
            raise ValidationError(
                _("Only a New request without bid or assignment history may be disposed.")
            )

    def action_accept_request(self):
        self._require_intake_manager()
        self.ensure_one()
        self._lock_for_bid_lifecycle()
        self._validate_new_intake_disposition()
        self._controlled_lifecycle_write({"status": "accepted"})
        self.message_post(body=_("Request accepted."))
        return True

    def action_open_decline_wizard(self):
        self._require_intake_manager()
        self.ensure_one()
        self._validate_new_intake_disposition()
        return {
            "type": "ir.actions.act_window",
            "name": _("Decline Request"),
            "res_model": "trucalc.order.decline.wizard",
            "view_mode": "form",
            "view_id": self.env.ref(
                "trucalc_orders.view_trucalc_order_decline_wizard_form"
            ).id,
            "target": "new",
            "context": {"default_order_id": self.id},
        }

    def action_decline_request(self, reason):
        self._require_intake_manager()
        self.ensure_one()
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError(_("A meaningful reason for decline is required."))
        reason = reason.strip()
        self._lock_for_bid_lifecycle()
        self._validate_new_intake_disposition()
        self._controlled_lifecycle_write({
            "status": "declined",
            "decline_reason": reason,
        })
        self.message_post(body=_("Request declined. Reason: %s") % escape(reason))
        return True

    @api.onchange("assigned_vendor_id", "service_type")
    def _onchange_vendor_fee(self):
        if not self.assigned_vendor_id:
            return

        fee_schedule = self.env["trucalc.vendor.fee"].search(
            [
                ("vendor_id", "=", self.assigned_vendor_id.id),
                ("service_type", "=", self.service_type),
            ],
            limit=1,
        )

        if fee_schedule:
            self.vendor_fee = fee_schedule.fee

    @api.onchange("reviewer_id")
    def _onchange_reviewer_fee(self):
        """
        Auto-populate review fee from reviewer fee schedule.

        Rules:
        1. Use service_type='review'
        2. Do not overwrite if fee_override=True
        3. Set fee to 0 if no review fee exists
        4. Allow manual edits after population
        """

        if self.fee_override:
            return

        if not self.reviewer_id:
            self.review_fee = 0.0
            return

        fee_schedule = self.env["trucalc.vendor.fee"].search(
            [
                ("vendor_id", "=", self.reviewer_id.id),
                ("service_type", "=", "review"),
            ],
            limit=1,
        )

        if fee_schedule:
            self.review_fee = fee_schedule.fee
        else:
            self.review_fee = 0.0

    @api.private
    def action_bid_requested(self):
        self._require_bid_manager()
        self.ensure_one()
        self._lock_for_bid_lifecycle()
        if (
            self.status != "accepted"
            or self.bidding_round != 0
            or self.invitation_ids
            or self.bid_ids
            or self.assigned_vendor_id
        ):
            raise ValidationError(_("Only an Accepted order without bid history may start bidding."))
        self._controlled_lifecycle_write({"status": "bid_requested", "bidding_round": 1})
        self.env["trucalc.bid.audit"]._log_event(
            "bidding_started", self,
            old_values={"status": "accepted", "bidding_round": 0},
            new_values={"status": "bid_requested", "bidding_round": 1},
        )
        return True

    @api.private
    def _eligible_solicitation_vendors(self):
        self.ensure_one()
        if self.service_type not in dict(SERVICE_SELECTION):
            return self.env["trucalc.vendor"].browse()
        fees = self.env["trucalc.vendor.fee"].search([
            ("service_type", "=", self.service_type),
            ("vendor_id.active", "=", True),
        ])
        return fees.mapped("vendor_id")

    @api.private
    def _validate_solicitation_vendors(self, vendors):
        self.ensure_one()
        vendors = vendors.exists()
        if not vendors:
            raise ValidationError(_("At least one eligible vendor is required."))
        if self.service_type not in dict(SERVICE_SELECTION):
            raise ValidationError(_("The order service type cannot be solicited."))
        fee_vendor_ids = set(self.env["trucalc.vendor.fee"].search([
            ("vendor_id", "in", vendors.ids),
            ("service_type", "=", self.service_type),
        ]).mapped("vendor_id").ids)
        invalid = vendors.filtered(
            lambda vendor: not vendor.active
            or vendor.id not in fee_vendor_ids
        )
        if invalid:
            raise ValidationError(_(
                "Every selected vendor must be active and have a matching standard fee."
            ))
        return vendors

    @api.private
    def _validate_future_deadline(self, deadline):
        parsed = fields.Datetime.to_datetime(deadline)
        if not parsed or parsed <= fields.Datetime.now():
            raise ValidationError(_("The Bid Response Deadline must be in the future."))
        return parsed

    @api.private
    def _format_response_deadline(self, deadline):
        return tools.format_datetime(self.env, deadline, dt_format="short")

    @api.private
    def action_request_vendor_bids(self, vendors, response_deadline):
        self._require_bid_manager()
        self.ensure_one()
        self._lock_for_bid_lifecycle()
        original_solicitation = self.status == "accepted" and self.bidding_round == 0
        reopened_solicitation = self._is_clean_reopened_unsolicited_round()
        if not original_solicitation and not reopened_solicitation:
            raise ValidationError(_(
                "Request Bids requires an Accepted original Order or a clean, "
                "unsolicited reopened bidding round."
            ))
        if not self.due_date:
            raise ValidationError(_("Set a Client Due Date before requesting Vendor bids."))
        vendors = self._validate_solicitation_vendors(vendors)
        deadline = self._validate_future_deadline(response_deadline)
        if original_solicitation:
            self.action_bid_requested()
        invitations = self.env["trucalc.bid.invitation"].create([
            {
                "order_id": self.id,
                "vendor_id": vendor.id,
                "response_deadline": deadline,
            }
            for vendor in vendors
        ])
        if len(invitations) != len(vendors):
            raise ValidationError(_("The complete vendor solicitation was not created."))
        vendor_names = ", ".join(vendors.mapped("name"))
        self.message_post(body=_(
            "Bid requests sent to %(vendors)s. Response deadline: %(deadline)s."
        ) % {
            "vendors": escape(vendor_names),
            "deadline": self._format_response_deadline(deadline),
        })
        self.env["trucalc.bid.audit"]._log_event(
            "solicitation_created", self,
            new_values={
                "vendor_ids": vendors.ids,
                "response_deadline": fields.Datetime.to_string(deadline),
            },
        )
        return True

    @api.private
    def _current_round_invitations(self):
        self.ensure_one()
        return self.env["trucalc.bid.invitation"].search([
            ("order_id", "=", self.id),
            ("round_number", "=", self.bidding_round),
            ("is_legacy_reconstructed", "=", False),
        ])

    @api.private
    def _current_round_solicitation_facts(self):
        self.ensure_one()
        if self.bidding_round <= 0:
            return (
                self.env["trucalc.bid.invitation"].browse(),
                self.env["trucalc.order.vendor.authorization"].browse(),
                self.env["trucalc.bid"].browse(),
            )
        invitations = self._current_round_invitations()
        authorizations = self.env[
            "trucalc.order.vendor.authorization"
        ].sudo().with_context(active_test=False).search([
            ("order_id", "=", self.id),
            ("round_number", "=", self.bidding_round),
            ("source", "=", "invitation"),
        ])
        bids = self.env["trucalc.bid"].search([
            ("order_id", "=", self.id),
            ("round_number", "=", self.bidding_round),
        ])
        return invitations, authorizations, bids

    @api.private
    def _is_clean_reopened_unsolicited_round(self):
        self.ensure_one()
        invitations, authorizations, bids = self._current_round_solicitation_facts()
        return bool(
            self.status == "bid_requested"
            and self.bidding_round > 0
            and not invitations
            and not authorizations
            and not bids
        )

    @api.private
    def _current_round_deadline(self):
        invitations = self._current_round_invitations()
        deadlines = set(invitations.mapped("response_deadline"))
        if not invitations or False in deadlines or len(deadlines) != 1:
            raise ValidationError(_("The current bidding round has no single response deadline."))
        return next(iter(deadlines))

    @api.private
    def action_add_vendor_bid_requests(self, vendors):
        self._require_bid_manager()
        self.ensure_one()
        self._lock_for_bid_lifecycle()
        if not self.due_date:
            raise ValidationError(_("Set a Client Due Date before adding Vendor bid requests."))
        if self.status != "bid_requested" or self.bidding_round <= 0:
            raise ValidationError(_("Only a Bid Requested order may add vendors."))
        deadline = self._current_round_deadline()
        self._validate_future_deadline(deadline)
        vendors = self._validate_solicitation_vendors(vendors)
        already_invited = self._current_round_invitations().mapped("vendor_id")
        if vendors & already_invited:
            raise ValidationError(_("A selected vendor is already invited for this round."))
        invitations = self.env["trucalc.bid.invitation"].create([
            {"order_id": self.id, "vendor_id": vendor.id,
             "response_deadline": deadline}
            for vendor in vendors
        ])
        if len(invitations) != len(vendors):
            raise ValidationError(_("The complete additional solicitation was not created."))
        vendor_names = ", ".join(vendors.mapped("name"))
        self.message_post(body=_(
            "Additional bid requests sent to %(vendors)s. Response deadline: %(deadline)s."
        ) % {
            "vendors": escape(vendor_names),
            "deadline": self._format_response_deadline(deadline),
        })
        self.env["trucalc.bid.audit"]._log_event(
            "vendors_added", self,
            new_values={"vendor_ids": vendors.ids,
                        "response_deadline": fields.Datetime.to_string(deadline)},
        )
        return True

    @api.private
    def action_extend_bid_deadline(self, new_deadline):
        self._require_bid_manager()
        self.ensure_one()
        self._lock_for_bid_lifecycle()
        if self.status != "bid_requested" or self.bidding_round <= 0:
            raise ValidationError(_("Only a Bid Requested order may extend its deadline."))
        old_deadline = self._current_round_deadline()
        deadline = self._validate_future_deadline(new_deadline)
        if deadline <= old_deadline:
            raise ValidationError(_("The new deadline must be later than the current deadline."))
        invitations = self._current_round_invitations()
        if any(invitation.state != "invited" for invitation in invitations):
            raise ValidationError(_("Only an active current bidding round may be extended."))
        for invitation in invitations:
            invitation.action_set_response_deadline(deadline)
        self.message_post(body=_(
            "Bid response deadline extended from %(old)s to %(new)s."
        ) % {
            "old": self._format_response_deadline(old_deadline),
            "new": self._format_response_deadline(deadline),
        })
        self.env["trucalc.bid.audit"]._log_event(
            "deadline_extended", self,
            old_values={"response_deadline": fields.Datetime.to_string(old_deadline)},
            new_values={"response_deadline": fields.Datetime.to_string(deadline)},
        )
        return True

    def action_open_request_bids_wizard(self):
        self._require_bid_manager()
        self.ensure_one()
        if not self.id or not (
            (self.status == "accepted" and self.bidding_round == 0)
            or self._is_clean_reopened_unsolicited_round()
        ):
            raise ValidationError(_(
                "Request Bids requires an Accepted original Order or a clean, "
                "unsolicited reopened bidding round."
            ))
        return self._solicitation_wizard_action("request")

    def action_open_manage_bid_requests_wizard(self):
        self._require_bid_manager()
        self.ensure_one()
        if self.status != "bid_requested" or not self._current_round_invitations():
            raise ValidationError(_(
                "The current bidding round must be solicited before managing bid requests."
            ))
        return self._solicitation_wizard_action("manage")

    @api.private
    def _solicitation_wizard_action(self, mode):
        return {
            "type": "ir.actions.act_window",
            "name": _("Request Bids") if mode == "request" else _("Manage Bid Requests"),
            "res_model": "trucalc.bid.request.wizard",
            "view_mode": "form",
            "view_id": self.env.ref("trucalc_orders.view_trucalc_bid_request_wizard_form").id,
            "target": "new",
            "context": {"default_order_id": self.id, "default_mode": mode},
        }

    def action_open_extend_bid_deadline_wizard(self):
        self._require_bid_manager()
        self.ensure_one()
        if self.status != "bid_requested" or not self._current_round_invitations():
            raise ValidationError(_(
                "The current bidding round must be solicited before extending its deadline."
            ))
        return {
            "type": "ir.actions.act_window",
            "name": _("Extend Bid Deadline"),
            "res_model": "trucalc.bid.deadline.wizard",
            "view_mode": "form",
            "view_id": self.env.ref("trucalc_orders.view_trucalc_bid_deadline_wizard_form").id,
            "target": "new",
            "context": {"default_order_id": self.id},
        }

    def action_assigned(self):
        raise AccessError(_("An order may only be assigned by selecting a submitted bid."))

    def action_report_received(self):
        self.status = "report_received"

    def action_reopen_bidding(self):
        self._require_bid_manager()
        self.ensure_one()
        self._lock_for_bid_lifecycle()
        if self.status not in ("assigned", "engaged"):
            raise ValidationError(_("Only an assigned or engaged order may reopen bidding."))
        active_engagement = self.env["trucalc.vendor.engagement"].sudo().search([
            ("order_id", "=", self.id), ("active", "=", True),
        ])
        if self.status == "engaged" and len(active_engagement) != 1:
            raise ValidationError(_(
                "An Engaged order requires exactly one active Vendor engagement."
            ))
        if active_engagement:
            self.env.cr.execute(
                "SELECT id FROM trucalc_vendor_engagement WHERE id = %s FOR UPDATE",
                (active_engagement.id,),
            )
            active_engagement.invalidate_recordset()
        old_status = self.status
        old_round = self.bidding_round
        old_vendor = self.assigned_vendor_id.id
        old_fee = self.vendor_fee
        old_delivery_date = self.vendor_delivery_date
        old_engaged_at = self.vendor_engaged_at
        if active_engagement:
            active_engagement.with_user(self.env.user)._close_for_reopen()
        self._controlled_lifecycle_write({
            "status": "bid_requested",
            "bidding_round": old_round + 1,
            "assigned_vendor_id": False,
            "vendor_fee": 0.0,
            "vendor_delivery_date": False,
            "vendor_engaged_at": False,
        })
        self.env["trucalc.order.vendor.authorization"]._deactivate(
            [("order_id", "=", self.id), ("source", "=", "assignment")],
            "reopened",
        )
        self.env["trucalc.bid.audit"]._log_event(
            "bidding_reopened", self,
            old_values={"status": old_status, "bidding_round": old_round,
                        "assigned_vendor_id": old_vendor, "vendor_fee": old_fee,
                        "vendor_delivery_date": fields.Date.to_string(old_delivery_date),
                        "vendor_engaged_at": fields.Datetime.to_string(old_engaged_at)},
            new_values={"status": "bid_requested", "bidding_round": old_round + 1,
                        "assigned_vendor_id": False, "vendor_fee": 0.0,
                        "vendor_delivery_date": False,
                        "vendor_engaged_at": False},
        )
        return True


    def action_open_engagement_decision_wizard(self):
        self._require_bid_manager()
        self.ensure_one()
        engagement = self.current_engagement_id
        if (
            not engagement
            or engagement.response_state != "delivery_change_requested"
        ):
            raise ValidationError(_("There is no pending delivery-change request."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Review Delivery Change"),
            "res_model": "trucalc.vendor.engagement.decision.wizard",
            "view_mode": "form",
            "view_id": self.env.ref(
                "trucalc_orders.view_trucalc_vendor_engagement_decision_wizard_form"
            ).id,
            "target": "new",
            "context": {"default_engagement_id": engagement.id},
        }

    @api.constrains("reviewer_id")
    def _check_reviewer_capability(self):
        for order in self.filtered("reviewer_id"):
            if not order.reviewer_id.active or not self.env[
                "trucalc.vendor.fee"
            ].search_count([
                ("vendor_id", "=", order.reviewer_id.id),
                ("service_type", "=", "review"),
            ]):
                raise ValidationError(_(
                    "The selected Reviewer must be active and have a Review standard fee."
                ))

    def action_assign_reviewer(self):
        eligible_state = self.filtered(lambda order: order.status == "report_received")
        eligible_state._check_reviewer_capability()
        if any(not order.reviewer_id for order in eligible_state):
            raise ValidationError(_("Select an eligible Reviewer before assignment."))
        self.status = "reviewer_assigned"

    def action_start_review(self):
        self.status = "under_review"

    def action_complete_review(self):
        self.status = "completed"

    def action_cancelled(self):
        if any(order.status in ("new", "declined") for order in self):
            raise ValidationError(_("New and Declined requests cannot be cancelled."))
        self.status = "cancelled"
