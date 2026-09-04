import re

from odoo import api, fields, models, tools, _
from odoo.exceptions import AccessError, ValidationError
from markupsafe import Markup, escape

from .vendor_fee import SERVICE_SELECTION


BANK_REQUEST_FIELDS = frozenset({
    "borrower", "property_address", "city", "zip_code", "loan_number",
    "service_type", "property_type", "due_date", "inspection_contact_name",
    "inspection_contact_phone", "inspection_contact_email", "notes",
    "service_area_id", "service_state_id", "service_county",
})
BANK_ORDER_CREATE_FIELDS = BANK_REQUEST_FIELDS - {
    "service_state_id", "service_county",
}
FEE_SNAPSHOT_FIELDS = frozenset({
    "agreed_fee", "fee_source", "negotiated_fee_id", "fee_locked_at",
    "fee_currency_id",
})



class EvaluationOrder(models.Model):
    _name = "trucalc.order"
    _description = "TruCalc Order"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    def _mail_get_operation_for_mail_message_operation(self, message_operation):
        operations = super()._mail_get_operation_for_mail_message_operation(
            message_operation
        )
        if message_operation == "create" and self.env.user._trucalc_has_bank_role():
            operations.update(dict.fromkeys(self, None))
        elif message_operation == "create" and self.env.user.has_group(
            "trucalc_orders.group_trucalc_reviewer"
        ):
            reviewer_readonly = (
                self._filtered_access("read") - self._filtered_access("write")
            )
            operations.update(dict.fromkeys(reviewer_readonly, "read"))
        return operations

    order_number = fields.Char(
        string="Order Number",
        required=True,
        copy=False,
        readonly=True,
        default="New",
    )

    borrower = fields.Char(
        string="Borrower",
        tracking=True,
    )

    property_address = fields.Char(
        string="Property Address",
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

    county = fields.Char(
        string="County",
        tracking=True,
    )

    service_area_id = fields.Many2one(
        "trucalc.service.area",
        string="Service Area",
        copy=False,
        index=True,
        ondelete="restrict",
    )
    pricing_state_id = fields.Many2one(
        "res.country.state", string="Pricing State", copy=False,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations",
    )
    pricing_county_area_id = fields.Many2one(
        "trucalc.service.area", string="Pricing County", copy=False,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations",
    )
    available_pricing_state_ids = fields.Many2many(
        "res.country.state", compute="_compute_pricing_selector_options",
        compute_sudo=True,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations",
    )
    available_pricing_county_area_ids = fields.Many2many(
        "trucalc.service.area", compute="_compute_pricing_selector_options",
        compute_sudo=True,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations",
    )
    available_pricing_service_area_ids = fields.Many2many(
        "trucalc.service.area", compute="_compute_pricing_selector_options",
        compute_sudo=True,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations",
    )
    available_internal_bank_ids = fields.Many2many(
        "res.company", compute="_compute_available_internal_banks",
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations",
    )
    is_internal_draft = fields.Boolean(
        compute="_compute_is_internal_draft", compute_sudo=True,
    )

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
        tracking=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True,
    )
    agreed_fee = fields.Monetary(
        string="Agreed Fee", currency_field="fee_currency_id", readonly=True,
        copy=False, tracking=True,
    )
    fee_source = fields.Selection(
        [("base", "Base"), ("negotiated", "Negotiated")],
        string="Fee Source", readonly=True, copy=False, tracking=True,
    )
    negotiated_fee_id = fields.Many2one(
        "trucalc.negotiated.fee", string="Negotiated Fee Source",
        readonly=True, copy=False, ondelete="restrict",
    )
    fee_locked_at = fields.Datetime(
        string="Fee Locked At", readonly=True, copy=False,
    )
    fee_currency_id = fields.Many2one(
        "res.currency", string="Fee Currency", readonly=True, copy=False,
        ondelete="restrict",
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

    @api.depends("pricing_state_id", "pricing_county_area_id")
    def _compute_pricing_selector_options(self):
        Area = self.env["trucalc.service.area"].sudo()
        active_areas = Area.search([("active", "=", True)])
        states = active_areas.mapped("state_id")
        for order in self:
            state_areas = active_areas.filtered(
                lambda area: area.state_id == order.pricing_state_id
            )
            county_representatives = Area.browse()
            seen = set()
            for area in state_areas.sorted(
                key=lambda item: (item.county_normalized, item.id)
            ):
                if area.county_normalized not in seen:
                    county_representatives |= area
                    seen.add(area.county_normalized)
            service_areas = state_areas.filtered(
                lambda area: order.pricing_county_area_id
                and area.county_normalized
                == order.pricing_county_area_id.county_normalized
            )
            order.available_pricing_state_ids = states
            order.available_pricing_county_area_ids = county_representatives
            order.available_pricing_service_area_ids = service_areas

    @api.depends("requestor_company_id")
    def _compute_available_internal_banks(self):
        allowed = self.env.user.company_ids
        for order in self:
            order.available_internal_bank_ids = allowed - order.requestor_company_id

    @api.depends("status", "requestor_id")
    def _compute_is_internal_draft(self):
        for order in self:
            order.is_internal_draft = bool(
                order.status == "draft"
                and order.requestor_id
                and not order.requestor_id.sudo().share
            )

    @api.onchange("pricing_state_id")
    def _onchange_pricing_state(self):
        for order in self:
            order.pricing_county_area_id = False
            order.service_area_id = False
            order.service_type = False
            order.state = False
            order.county = False

    @api.onchange("pricing_county_area_id")
    def _onchange_pricing_county(self):
        for order in self:
            order.service_area_id = False
            order.service_type = False
            order.state = (
                order.pricing_state_id.code or order.pricing_state_id.name
                if order.pricing_state_id else False
            )
            order.county = (
                order.pricing_county_area_id.county
                if order.pricing_county_area_id else False
            )

    @api.onchange("service_area_id")
    def _onchange_service_area(self):
        for order in self:
            if order.service_area_id and not order.fee_locked_at:
                area = order.service_area_id
                if order.pricing_state_id != area.state_id:
                    order.pricing_state_id = area.state_id
                if (
                    not order.pricing_county_area_id
                    or order.pricing_county_area_id.county_normalized
                    != area.county_normalized
                ):
                    order.pricing_county_area_id = area
                order.service_type = area.service_type
                order.state = (
                    area.state_id.code
                    or area.state_id.name
                )
                order.county = area.county

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

    inspection_contact_name = fields.Char(
        string="Inspection Contact Person", copy=False, tracking=True,
    )

    inspection_contact_phone = fields.Char(
        string="Inspection Contact Phone", copy=False, tracking=True,
    )

    inspection_contact_phone_display = fields.Char(
        string="Inspection Contact Phone",
        compute="_compute_inspection_contact_phone_display",
        inverse="_inverse_inspection_contact_phone_display",
    )

    inspection_contact_email = fields.Char(
        string="Inspection Contact Email", copy=False, tracking=True,
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
            ("draft", "Draft"),
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
        string="Commercial Reviewer",
        tracking=True,
        domain="[('active', '=', True), ('fee_schedule_ids.service_type', '=', 'review')]",
    )

    reviewer_user_id = fields.Many2one(
        "res.users",
        string="Reviewer",
        tracking=True,
        domain="[('active', '=', True), ('share', '=', False)]",
    )
    valuation_approved = fields.Boolean(
        compute="_compute_vendor_deliverables", compute_sudo=True, readonly=True,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations,trucalc_orders.group_trucalc_reviewer",
    )
    valuation_approved_at = fields.Datetime(
        compute="_compute_vendor_deliverables", compute_sudo=True, readonly=True,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations,trucalc_orders.group_trucalc_reviewer",
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

    lifecycle_event_ids = fields.One2many(
        "trucalc.order.lifecycle.event", "order_id",
        string="Lifecycle Events", readonly=True, copy=False,
    )

    current_valuation_id = fields.Many2one(
        "trucalc.vendor.deliverable", compute="_compute_vendor_deliverables",
        compute_sudo=True, readonly=True,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations,trucalc_orders.group_trucalc_reviewer",
    )
    vendor_invoice_id = fields.Many2one(
        "trucalc.vendor.deliverable", compute="_compute_vendor_deliverables",
        compute_sudo=True, readonly=True,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations",
    )
    valuation_filename_link = fields.Html(
        compute="_compute_vendor_deliverables", compute_sudo=True, sanitize=False,
        readonly=True, string="Valuation",
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations,trucalc_orders.group_trucalc_reviewer",
    )
    vendor_invoice_filename_link = fields.Html(
        compute="_compute_vendor_deliverables", compute_sudo=True, sanitize=False,
        readonly=True, string="Vendor Invoice",
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations",
    )
    previous_valuation_count = fields.Integer(
        compute="_compute_vendor_deliverables", compute_sudo=True, readonly=True,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations,trucalc_orders.group_trucalc_reviewer",
    )
    valuation_submitted_at = fields.Datetime(
        compute="_compute_vendor_deliverables", compute_sudo=True, readonly=True,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations,trucalc_orders.group_trucalc_reviewer",
    )
    valuation_deliverable_status = fields.Selection(
        [("submitted", "Submitted")], compute="_compute_vendor_deliverables",
        compute_sudo=True, readonly=True, string="Valuation Status",
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations,trucalc_orders.group_trucalc_reviewer",
    )
    valuation_version = fields.Integer(
        compute="_compute_vendor_deliverables", compute_sudo=True, readonly=True,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations,trucalc_orders.group_trucalc_reviewer",
    )
    valuation_revision_requested = fields.Boolean(
        compute="_compute_vendor_deliverables", compute_sudo=True, readonly=True,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations,trucalc_orders.group_trucalc_reviewer",
    )
    valuation_revision_instructions = fields.Text(
        compute="_compute_vendor_deliverables", compute_sudo=True, readonly=True,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations,trucalc_orders.group_trucalc_reviewer",
    )
    vendor_invoice_submitted_at = fields.Datetime(
        compute="_compute_vendor_deliverables", compute_sudo=True, readonly=True,
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations",
    )
    vendor_invoice_deliverable_status = fields.Selection(
        [("submitted", "Submitted")], compute="_compute_vendor_deliverables",
        compute_sudo=True, readonly=True, string="Vendor Invoice Status",
        groups="trucalc_orders.group_trucalc_admin,trucalc_orders.group_trucalc_operations",
    )

    def _compute_vendor_deliverables(self):
        Deliverable = self.env["trucalc.vendor.deliverable"].sudo()
        grouped = {}
        if self.ids:
            for deliverable in Deliverable.search([
                ("order_id", "in", self.ids),
            ], order="version desc, id desc"):
                grouped.setdefault(deliverable.order_id.id, []).append(deliverable)
        for order in self:
            deliverables = grouped.get(order.id, [])
            valuation = next((item for item in deliverables if (
                item.artifact_type == "valuation" and item.is_current
            )), Deliverable.browse())
            invoice = next((item for item in deliverables if (
                item.artifact_type == "vendor_invoice"
            )), Deliverable.browse())
            order.current_valuation_id = valuation
            order.vendor_invoice_id = invoice
            order.valuation_filename_link = valuation.filename_link if valuation else False
            order.vendor_invoice_filename_link = invoice.filename_link if invoice else False
            order.previous_valuation_count = len([
                item for item in deliverables
                if item.artifact_type == "valuation" and not item.is_current
            ])
            order.valuation_submitted_at = valuation.submitted_at if valuation else False
            order.valuation_deliverable_status = valuation.status if valuation else False
            revision_request = self.env[
                "trucalc.order.lifecycle.event"
            ]._open_valuation_revision_request(valuation) if valuation else self.env[
                "trucalc.order.lifecycle.event"
            ].browse()
            order.valuation_version = valuation.version if valuation else 0
            order.valuation_revision_requested = bool(revision_request)
            order.valuation_revision_instructions = (
                revision_request.vendor_revision_instructions
                if revision_request else False
            )
            approval = self.env[
                "trucalc.order.lifecycle.event"
            ]._valuation_approval(valuation) if valuation else self.env[
                "trucalc.order.lifecycle.event"
            ].browse()
            order.valuation_approved = bool(approval)
            order.valuation_approved_at = approval.event_at if approval else False
            order.vendor_invoice_submitted_at = invoice.submitted_at if invoice else False
            order.vendor_invoice_deliverable_status = invoice.status if invoice else False

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

    @api.depends("inspection_contact_phone")
    def _compute_inspection_contact_phone_display(self):
        for order in self:
            order.inspection_contact_phone_display = (
                self._format_inspection_contact_phone(
                    order.inspection_contact_phone
                )
            )

    def _inverse_inspection_contact_phone_display(self):
        for order in self:
            order.inspection_contact_phone = (
                self._normalize_inspection_contact_phone(
                    order.inspection_contact_phone_display
                )
            )

    @api.model
    def _inspection_contact_phone_parts(self, value):
        phone = value if isinstance(value, str) else ""
        phone = re.sub(r"[\x00-\x1f\x7f]+", " ", phone)
        phone = re.sub(r"\s+", " ", phone).strip()
        extension = False
        extension_match = re.search(
            r"(?i)\s*(?:ext\.?|x)\s*[:#.-]?\s*(\d{1,10})\s*$", phone
        )
        base_phone = phone
        if extension_match:
            extension = extension_match.group(1)
            base_phone = phone[:extension_match.start()].strip()
        digits = "".join(re.findall(r"\d", base_phone))
        us_digits = digits
        if len(us_digits) == 11 and us_digits.startswith("1"):
            us_digits = us_digits[1:]
        explicit_non_us_country = base_phone.startswith("+") and not (
            len(digits) == 11 and digits.startswith("1")
        )
        return phone, base_phone, digits, us_digits, extension, explicit_non_us_country

    @api.model
    def _format_inspection_contact_phone(self, value):
        (
            phone, _base_phone, _digits, us_digits, extension,
            explicit_non_us_country,
        ) = self._inspection_contact_phone_parts(value)
        if not phone:
            return False
        if len(us_digits) == 10 and not explicit_non_us_country:
            formatted = "(%s) %s-%s" % (
                us_digits[:3], us_digits[3:6], us_digits[6:],
            )
            if extension:
                formatted += " ext. %s" % extension
            return formatted
        return phone

    @api.model
    def _normalize_inspection_contact_phone(self, value, required=False):
        (
            phone, base_phone, digits, us_digits, extension,
            explicit_non_us_country,
        ) = self._inspection_contact_phone_parts(value)
        if required and not phone:
            raise ValidationError(_("Inspection Contact Phone is required."))
        if not phone:
            return False
        if len(phone) > 64:
            raise ValidationError(_(
                "Inspection Contact Phone may not exceed 64 characters."
            ))

        if len(us_digits) == 10 and not explicit_non_us_country:
            normalized = us_digits
            if extension:
                normalized += " ext %s" % extension
            return normalized
        if len(digits) < 7:
            raise ValidationError(_("Enter a usable Inspection Contact Phone."))
        normalized = base_phone
        if extension:
            normalized += " ext %s" % extension
        return normalized

    @api.model_create_multi
    def create(self, vals_list):
        user = self.env.user
        bank_company = False
        if user._trucalc_has_bank_role():
            bank_company = user._trucalc_bank_identity()
        internal_draft = bool(
            self.env.context.get("trucalc_internal_draft_intake")
            and not user._trucalc_has_external_role()
            and (
                user.has_group("trucalc_orders.group_trucalc_admin")
                or user.has_group("trucalc_orders.group_trucalc_operations")
            )
        )
        authoritative_order_date = fields.Date.context_today(self)
        for vals in vals_list:
            if FEE_SNAPSHOT_FIELDS.intersection(vals):
                raise AccessError(_("Order fee snapshots are server-controlled."))
            if "vendor_authorization_ids" in vals:
                raise AccessError(_("Vendor Order authorization is server-maintained."))
            if "order_date" in vals:
                raise AccessError(_("Order Date is system-controlled."))
            if bank_company:
                forbidden = set(vals) - BANK_ORDER_CREATE_FIELDS - {
                    "company_id", "requestor_company_id", "requestor_id",
                }
                if forbidden or vals.get("loan_amount"):
                    raise AccessError(_("The Bank request contains unauthorized Order fields."))
                service_area = self.env["trucalc.service.area"].sudo().browse(
                    vals.get("service_area_id")
                ).exists()
                if (
                    len(service_area) != 1 or not service_area.active
                    or service_area.service_type != vals.get("service_type")
                ):
                    raise AccessError(_("The selected Service Area is not authorized."))
                vals.update({
                    "state": service_area.state_id.code or service_area.state_id.name,
                    "county": service_area.county,
                    "service_area_id": service_area.id,
                })
            if not internal_draft:
                if not vals.get("borrower"):
                    raise ValidationError(_("Borrower is required."))
                if not vals.get("property_address"):
                    raise ValidationError(_("Property Address is required."))
                if not vals.get("due_date"):
                    raise ValidationError(_("Client Due Date is required."))
            vals["order_date"] = False if internal_draft else authoritative_order_date
            if internal_draft:
                company = self.env["res.company"].browse(
                    vals.get("company_id")
                ).exists()
                if company and (
                    company == self.env.company
                    or company not in user.company_ids
                ):
                    raise ValidationError(_(
                        "Select an authorized customer Bank other than TruCalc."
                    ))
                vals.update({
                    "company_id": company.id or False,
                    "requestor_company_id": self.env.company.id,
                    "requestor_id": user.id,
                    "status": "draft",
                })
            if (
                vals.get("status", "new") != ("draft" if internal_draft else "new")
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
            if "inspection_contact_phone" in vals:
                vals["inspection_contact_phone"] = (
                    self._normalize_inspection_contact_phone(
                        vals["inspection_contact_phone"]
                    )
                )
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
            if internal_draft:
                order.sudo().message_post(body=Markup(_(
                    "<p><strong>Internal Draft Created</strong></p>"
                    "<ul><li>Created By: %(actor)s</li>"
                    "<li>Created At: %(created_at)s</li></ul>"
                )) % {
                    "actor": escape(user.name),
                    "created_at": tools.format_datetime(
                        order.env, order.create_date, tz=user.tz, dt_format="medium"
                    ),
                })
                continue
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

    @api.model
    @api.private
    def _resolve_bank_fee(self, bank, service_area):
        bank = bank.sudo().exists()
        service_area = service_area.sudo().exists()
        if len(bank) != 1 or bank._name != "res.company":
            raise ValidationError(_("A valid Bank is required to resolve pricing."))
        if len(service_area) != 1 or not service_area.active:
            raise ValidationError(_(
                "An active Service Area is required to resolve pricing."
            ))
        if bank.currency_id != service_area.currency_id:
            raise ValidationError(_(
                "The Bank and Service Area currencies do not match."
            ))
        schedules = self.env["trucalc.negotiated.fee"].sudo().search([
            ("bank_id", "=", bank.id),
            ("service_area_id", "=", service_area.id),
            ("active", "=", True),
        ], limit=2)
        if len(schedules) > 1:
            raise ValidationError(_("Negotiated pricing is ambiguous."))
        if schedules:
            return {
                "agreed_fee": schedules.negotiated_fee,
                "fee_source": "negotiated",
                "service_area_id": service_area.id,
                "negotiated_fee_id": schedules.id,
                "fee_currency_id": service_area.currency_id.id,
            }
        self.env.cr.execute(
            "SELECT base_fee FROM trucalc_service_area WHERE id = %s",
            (service_area.id,),
        )
        row = self.env.cr.fetchone()
        if not row or row[0] is None:
            raise ValidationError(_(
                "No Bank fee is configured for the selected Service Area."
            ))
        return {
            "agreed_fee": row[0],
            "fee_source": "base",
            "service_area_id": service_area.id,
            "negotiated_fee_id": False,
            "fee_currency_id": service_area.currency_id.id,
        }

    @api.model
    @api.private
    def _lock_pricing_key(self, bank_id, service_area_id):
        """Serialize one logical Bank + Service Area pricing configuration."""
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ("trucalc.pricing:%s:%s" % (bank_id, service_area_id),),
        )

    @api.model
    @api.private
    def _resolve_and_lock_bank_fee(self, bank, service_area):
        """Resolve pricing after narrowly locking its complete configuration key."""
        bank = bank.sudo().exists()
        service_area = service_area.sudo().exists()
        if len(bank) != 1 or len(service_area) != 1:
            return self._resolve_bank_fee(bank, service_area)
        self._lock_pricing_key(bank.id, service_area.id)
        self.env.cr.execute(
            "SELECT id FROM res_company WHERE id = %s FOR SHARE", (bank.id,),
        )
        self.env.cr.execute(
            "SELECT id FROM trucalc_service_area WHERE id = %s FOR SHARE",
            (service_area.id,),
        )
        bank.invalidate_recordset(["currency_id"])
        service_area.invalidate_recordset(["active", "base_fee", "currency_id"])
        pricing = self._resolve_bank_fee(bank, service_area)
        if pricing["negotiated_fee_id"]:
            self.env.cr.execute(
                "SELECT id FROM trucalc_negotiated_fee WHERE id = %s FOR SHARE",
                (pricing["negotiated_fee_id"],),
            )
            self.env["trucalc.negotiated.fee"].browse(
                pricing["negotiated_fee_id"]
            ).invalidate_recordset(["active", "negotiated_fee", "currency_id"])
            pricing = self._resolve_bank_fee(bank, service_area)
        return pricing

    @api.model
    @api.private
    def _require_bank_draft_actor(self, actor):
        actor = actor.sudo().exists()
        if len(actor) != 1 or actor != self.env.user or not actor.active:
            raise AccessError(_("The Bank Draft operation is not authorized."))
        bank = actor._trucalc_bank_identity()
        if not (
            actor.has_group("trucalc_orders.group_bank_admin")
            or actor.has_group("trucalc_orders.group_bank_requestor")
        ):
            raise AccessError(_("The Bank Draft operation is not authorized."))
        return actor, bank

    @api.model
    @api.private
    def _authorize_bank_draft(self, order, actor):
        actor, bank = self._require_bank_draft_actor(actor)
        order = order.sudo().exists()
        is_admin = actor.has_group("trucalc_orders.group_bank_admin")
        if (
            len(order) != 1
            or order.status != "draft"
            or order.company_id != bank
            or order.requestor_company_id != bank
            or (not is_admin and order.requestor_id != actor)
        ):
            raise AccessError(_("The Bank Draft operation is not authorized."))
        return order, actor, bank

    @api.model
    @api.private
    def _prepare_bank_request_values(self, values, actor, final=False):
        if not isinstance(values, dict) or set(values) - BANK_REQUEST_FIELDS:
            raise AccessError(_("The Bank request contains unauthorized Order fields."))

        def clean_text(field_name, label, maximum=255, required=False):
            value = values.get(field_name)
            value = value.strip() if isinstance(value, str) else ""
            if required and not value:
                raise ValidationError(_("%(label)s is required.", label=label))
            if len(value) > maximum:
                raise ValidationError(_(
                    "%(label)s may not exceed %(maximum)s characters.",
                    label=label, maximum=maximum,
                ))
            return value or False

        prepared = {
            "borrower": clean_text("borrower", "Borrower", required=True),
            "property_address": clean_text(
                "property_address", "Property Address", 512, required=True,
            ),
            "city": clean_text("city", "City", required=final),
            "zip_code": clean_text("zip_code", "ZIP", 20, required=final),
            "loan_number": clean_text(
                "loan_number", "Loan Number", required=final,
            ),
            "inspection_contact_name": clean_text(
                "inspection_contact_name", "Inspection Contact Person",
                required=final,
            ),
        }

        property_type = clean_text(
            "property_type", "Property Type", 64, required=final,
        )
        if property_type and property_type not in dict(
            self._fields["property_type"].selection
        ):
            raise ValidationError(_("Select a valid Property Type."))
        prepared["property_type"] = property_type

        service_type = clean_text(
            "service_type", "Service Type", 64, required=final,
        )
        if service_type and service_type not in dict(SERVICE_SELECTION):
            raise ValidationError(_("Select a valid Service Type."))

        area_input = values.get("service_area_id")
        state_input = values.get("service_state_id")
        county_input = values.get("service_county")
        has_area_input = bool(area_input or service_type)
        if has_area_input:
            try:
                service_area_id = int(area_input)
                state_id = int(state_input)
            except (TypeError, ValueError):
                raise ValidationError(_(
                    "Complete the State, County, and Service Type selection."
                ))
            service_area = self.env["trucalc.service.area"].sudo().browse(
                service_area_id
            ).exists()
            county_input = county_input.strip() if isinstance(county_input, str) else ""
            if (
                len(service_area) != 1
                or not service_area.active
                or service_area.state_id.id != state_id
                or service_area.county != county_input
                or service_area.service_type != service_type
            ):
                raise ValidationError(_(
                    "The selected State, County, and Service Type are not available."
                ))
            prepared.update({
                "service_type": service_type,
                "service_area_id": service_area.id,
                "state": service_area.state_id.code or service_area.state_id.name,
                "county": service_area.county,
            })
        else:
            if final:
                raise ValidationError(_("Select an active Service Area."))
            prepared.update({
                "service_type": False, "service_area_id": False,
                "state": False, "county": False,
            })

        due_input = values.get("due_date")
        try:
            due_date = fields.Date.to_date(due_input) if due_input else False
        except (TypeError, ValueError):
            due_date = False
        if final and not due_date:
            raise ValidationError(_("Enter a valid Client Due Date."))
        if due_input and not due_date:
            raise ValidationError(_("Enter a valid Client Due Date."))
        if due_date and due_date < fields.Date.context_today(self.with_user(actor)):
            raise ValidationError(_("Client Due Date may not be in the past."))
        prepared["due_date"] = due_date

        prepared["inspection_contact_phone"] = (
            self._normalize_inspection_contact_phone(
                values.get("inspection_contact_phone"), required=final,
            )
        )
        email_input = clean_text(
            "inspection_contact_email", "Inspection Contact Email", 254,
            required=final,
        )
        email = tools.email_normalize(email_input) if email_input else False
        if email_input and not email:
            raise ValidationError(_("Enter a valid Inspection Contact Email."))
        prepared["inspection_contact_email"] = email
        notes = values.get("notes")
        notes = notes.strip() if isinstance(notes, str) else False
        if notes and len(notes) > 5000:
            raise ValidationError(_("Notes / Instructions may not exceed 5000 characters."))
        prepared["notes"] = notes
        return prepared

    @api.model
    @api.private
    def _create_bank_draft(self, values, actor):
        actor, bank = self._require_bank_draft_actor(actor)
        create_values = self._prepare_bank_request_values(values, actor, final=False)
        create_values.update({
            "company_id": bank.id,
            "requestor_company_id": bank.id,
            "requestor_id": actor.id,
            "status": "draft",
            "order_date": False,
            "order_number": self.env.ref(
                "trucalc_orders.seq_trucalc_order"
            ).sudo().next_by_id() or "New",
        })
        trusted_model = self.with_user(actor).sudo().with_context(
            allowed_company_ids=[]
        )
        order = super(EvaluationOrder, trusted_model).create(create_values)
        order.sudo().message_post(body=Markup(_(
            "<p><strong>Bank Draft Created</strong></p><ul>"
            "<li>Order Number: %(order)s</li><li>Bank: %(bank)s</li>"
            "<li>Created By: %(requestor)s</li><li>Created At: %(created)s</li>"
            "</ul>"
        )) % {
            "order": escape(order.order_number),
            "bank": escape(order.company_id.name),
            "requestor": escape(actor.name),
            "created": escape(tools.format_datetime(
                order.env, fields.Datetime.now(), tz=actor.tz, dt_format="medium"
            )),
        })
        return order

    @api.model
    @api.private
    def _update_bank_draft(self, order, values, actor):
        order, actor, _bank = self._authorize_bank_draft(order, actor)
        order._lock_bank_draft()
        order, actor, _bank = self._authorize_bank_draft(order, actor)
        update_values = self._prepare_bank_request_values(values, actor, final=False)
        super(EvaluationOrder, order).write(update_values)
        order.message_post(body=Markup(_(
            "<p><strong>Bank Draft Updated</strong></p><ul>"
            "<li>Updated By: %(actor)s</li><li>Updated At: %(updated)s</li>"
            "</ul>"
        )) % {
            "actor": escape(actor.name),
            "updated": escape(tools.format_datetime(
                order.env, fields.Datetime.now(), tz=actor.tz, dt_format="medium"
            )),
        })
        return order

    @api.model
    @api.private
    def _send_bank_draft(self, order, values, actor):
        order, actor, bank = self._authorize_bank_draft(order, actor)
        order._lock_bank_draft()
        order, actor, bank = self._authorize_bank_draft(order, actor)
        send_values = self._prepare_bank_request_values(values, actor, final=True)
        send_values.update(self._resolve_and_lock_bank_fee(
            bank, self.env["trucalc.service.area"].browse(
                send_values["service_area_id"]
            ),
        ))
        send_values.update({
            "status": "new",
            "order_date": fields.Date.context_today(order.with_user(actor)),
            "fee_locked_at": fields.Datetime.now(),
        })
        super(EvaluationOrder, order).write(send_values)
        self.env["trucalc.order.lifecycle.event"]._log_event(
            order, "bank_request_sent", "draft", "new", actor,
        )
        order.message_post(body=Markup(_(
            "<p><strong>Bank Request Sent</strong></p><ul>"
            "<li>Order Number: %(order)s</li><li>Bank: %(bank)s</li>"
            "<li>Sent By: %(actor)s</li><li>Sent At: %(sent)s</li>"
            "<li>Transition: Draft → New</li></ul>"
        )) % {
            "order": escape(order.order_number),
            "bank": escape(order.company_id.name),
            "actor": escape(actor.name),
            "sent": escape(tools.format_datetime(
                order.env, fields.Datetime.now(), tz=actor.tz, dt_format="medium"
            )),
        })
        return order

    @api.private
    def _lock_bank_draft(self):
        self.ensure_one()
        self.flush_recordset(["status", "company_id", "requestor_company_id", "requestor_id"])
        self.env.cr.execute(
            "SELECT id FROM trucalc_order WHERE id = %s FOR UPDATE", (self.id,)
        )
        self.invalidate_recordset()

    def write(self, vals):
        if FEE_SNAPSHOT_FIELDS.intersection(vals):
            raise AccessError(_("Order fee snapshots are server-controlled."))
        if "reviewer_user_id" in vals:
            raise AccessError(_(
                "Reviewer assignment requires the controlled assignment action."
            ))
        self._check_operational_edit()
        self.invalidate_recordset([
            "status", "company_id", "requestor_company_id", "requestor_id",
            "fee_locked_at", "is_internal_draft",
        ])
        if "company_id" in vals:
            company = self.env["res.company"].browse(vals["company_id"]).exists()
            for order in self:
                if order.company_id == company:
                    continue
                if not order.is_internal_draft or order.fee_locked_at:
                    raise AccessError(_(
                        "The Bank/Client Company is immutable after Order creation."
                    ))
                if company and (
                    company == order.requestor_company_id
                    or company not in self.env.user.company_ids
                ):
                    raise ValidationError(_(
                        "Select an authorized customer Bank other than TruCalc."
                    ))
        if "vendor_authorization_ids" in vals:
            raise AccessError(_("Vendor Order authorization is server-maintained."))
        if "decline_reason" in vals:
            raise AccessError(_("The decline reason requires the controlled decline action."))
        if "order_date" in vals:
            raise AccessError(_("Order Date is system-controlled."))
        if "status" in vals:
            raise AccessError(_(
                "Order status may only be changed through an authorized workflow action."
            ))
        if (
            "due_date" in vals and not vals["due_date"]
            and any(not order.is_internal_draft for order in self)
        ):
            raise ValidationError(_("Client Due Date is required."))
        for field_name, label in (
            ("borrower", "Borrower"),
            ("property_address", "Property Address"),
        ):
            if (
                field_name in vals and not vals[field_name]
                and any(not order.is_internal_draft for order in self)
            ):
                raise ValidationError(_("%(label)s is required.", label=label))
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
        if "inspection_contact_phone" in vals:
            vals["inspection_contact_phone"] = (
                self._normalize_inspection_contact_phone(
                    vals["inspection_contact_phone"]
                )
            )
        if (
            {
                "service_area_id", "service_type", "state", "county",
                "pricing_state_id", "pricing_county_area_id",
            }.intersection(vals)
            and any(order.fee_locked_at for order in self)
        ):
            raise AccessError(_(
                "Order service and area are immutable after the fee is locked."
            ))
        result = super().write(vals)
        return result

    @api.private
    def _check_operational_edit(self):
        """Serialize ordinary edits with completion; no context bypass."""
        for order in self.sudo().sorted("id"):
            order._lock_for_bid_lifecycle()
            if order.status == "completed":
                raise AccessError(_("Completed Orders are closed to operational editing."))

    @api.private
    def _controlled_lifecycle_write(self, vals):
        return super(EvaluationOrder, self).write(vals)

    @api.private
    def _transition_status(
        self, from_status, to_status, event_type, values=None, deliverable=False,
        actor=False,
    ):
        self.ensure_one()
        actor = actor or self.env.user
        order = self.sudo()
        order._lock_for_bid_lifecycle()
        if order.status != from_status:
            raise ValidationError(_(
                "This lifecycle action is no longer valid for the current Order state."
            ))
        transition_values = dict(values or {}, status=to_status)
        super(EvaluationOrder, order).write(transition_values)
        self.env["trucalc.order.lifecycle.event"]._log_event(
            order, event_type, from_status, to_status, actor,
            deliverable=deliverable,
        )
        return True

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

    def action_add_document(self):
        self._require_intake_manager()
        self.ensure_one()
        self._check_operational_edit()
        if self.status == "draft" and not self.is_internal_draft:
            raise AccessError(_("TruCalc personnel may not modify a Bank Draft."))
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Add Document"),
            "res_model": "trucalc.document",
            "view_mode": "form",
            "view_id": self.env.ref("trucalc_orders.view_trucalc_document_form").id,
            "target": "new",
            "context": {
                "default_order_id": self.id,
                "default_origin": "trucalc",
                "trucalc_document_order_id": self.id,
            },
        }

    def action_accept_request(self):
        self._require_intake_manager()
        self.ensure_one()
        self._lock_for_bid_lifecycle()
        self._validate_new_intake_disposition()
        self._controlled_lifecycle_write({"status": "accepted"})
        self.message_post(body=_("Request accepted."))
        return True

    def action_submit_internal_draft(self):
        self._require_intake_manager()
        self.ensure_one()
        self._lock_for_bid_lifecycle()
        self.invalidate_recordset()
        if not self.is_internal_draft:
            raise ValidationError(_("Only an internal Draft may be submitted."))
        if (
            not self.company_id
            or self.company_id == self.requestor_company_id
            or self.company_id not in self.env.user.company_ids
        ):
            raise ValidationError(_(
                "Select an authorized customer Bank other than TruCalc before submitting."
            ))
        if not self.borrower or not self.property_address or not self.due_date:
            raise ValidationError(_(
                "Borrower, Property Address, and Client Due Date are required before submitting."
            ))
        area = self.service_area_id
        if (
            not self.pricing_state_id
            or not self.pricing_county_area_id
            or not self.service_type
            or not area or not area.active
            or area.state_id != self.pricing_state_id
            or area.county_normalized
            != self.pricing_county_area_id.county_normalized
            or area.service_type != self.service_type
            or self.state != (area.state_id.code or area.state_id.name)
            or self.county != area.county
        ):
            raise ValidationError(_(
                "Select a valid active State, County, and Service Type before submitting."
            ))
        values = self._resolve_and_lock_bank_fee(self.company_id, area)
        values.update({
            "status": "new",
            "order_date": fields.Date.context_today(self),
            "fee_locked_at": fields.Datetime.now(),
        })
        self._controlled_lifecycle_write(values)
        self.env["trucalc.order.lifecycle.event"]._log_event(
            self, "internal_request_submitted", "draft", "new", self.env.user,
        )
        self.message_post(body=_("Internal Draft submitted as a New request."))
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
        raise AccessError(_(
            "Report receipt is unavailable until the controlled report-delivery workflow."
        ))

    def action_view_previous_valuations(self):
        self.ensure_one()
        self.check_access("read")
        user = self.env.user
        if not (
            user.has_group("trucalc_orders.group_trucalc_admin")
            or user.has_group("trucalc_orders.group_trucalc_operations")
            or (user.has_group("trucalc_orders.group_trucalc_reviewer")
                and self.status == "completed" and self.reviewer_user_id == user)
        ):
            raise AccessError(_("Valuation history access is not authorized."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Previous Valuation Versions"),
            "res_model": "trucalc.vendor.deliverable",
            "view_mode": "list,form",
            "views": [
                (self.env.ref(
                    "trucalc_orders.view_trucalc_vendor_deliverable_history_list"
                ).id, "list"),
                (self.env.ref(
                    "trucalc_orders.view_trucalc_vendor_deliverable_history_form"
                ).id, "form"),
            ],
            "domain": [
                ("order_id", "=", self.id),
                ("artifact_type", "=", "valuation"),
                ("is_current", "=", False),
            ],
            "target": "new",
            "context": {"create": False, "edit": False, "delete": False},
        }

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

    @api.constrains("reviewer_user_id")
    def _check_reviewer_user(self):
        for order in self.filtered("reviewer_user_id"):
            order.reviewer_user_id._trucalc_reviewer_identity()

    def action_open_reviewer_assignment_wizard(self):
        self._require_intake_manager()
        self.ensure_one()
        if self.status not in ("report_received", "reviewer_assigned", "under_review"):
            raise ValidationError(_("Reviewer assignment is not available in this state."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Assign Reviewer") if self.status == "report_received" else _("Reassign Reviewer"),
            "res_model": "trucalc.reviewer.assignment.wizard",
            "view_mode": "form",
            "view_id": self.env.ref(
                "trucalc_orders.view_trucalc_reviewer_assignment_wizard_form"
            ).id,
            "target": "new",
            "context": {"default_order_id": self.id},
        }

    def action_assign_reviewer(self, reviewer_user=False):
        self._require_intake_manager()
        self.ensure_one()
        self._lock_for_bid_lifecycle()
        if self.status != "report_received":
            raise ValidationError(_(
                "A Reviewer may be assigned only after controlled report receipt."
            ))
        reviewer_user = (
            reviewer_user.sudo().exists()
            if reviewer_user else self.reviewer_user_id.sudo().exists()
        )
        if len(reviewer_user) != 1:
            raise ValidationError(_("Select an internal Reviewer before assignment."))
        reviewer_user._trucalc_reviewer_identity()
        return self._transition_status(
            "report_received", "reviewer_assigned", "reviewer_assigned",
            {"reviewer_user_id": reviewer_user.id, "reviewer_id": False, "review_fee": 0.0},
        )

    def action_reassign_reviewer(self, reviewer_user, reason):
        self._require_intake_manager()
        self.ensure_one()
        reason = reason.strip() if isinstance(reason, str) else ""
        if not reason:
            raise ValidationError(_("A reassignment reason is required."))
        reviewer_user = reviewer_user.sudo().exists()
        if len(reviewer_user) != 1:
            raise ValidationError(_("Select an internal Reviewer."))
        reviewer_user._trucalc_reviewer_identity()
        self._lock_for_bid_lifecycle()
        order = self.sudo()
        prior = order.reviewer_user_id
        if order.status not in ("reviewer_assigned", "under_review") or not prior:
            raise ValidationError(_("Reviewer reassignment is not available in this state."))
        if reviewer_user == prior:
            raise ValidationError(_("Select a different Reviewer."))
        if order.status == "under_review" and self.env[
            "trucalc.order.lifecycle.event"
        ]._valuation_approval(order.current_valuation_id):
            raise ValidationError(_("An approved Valuation prevents Reviewer reassignment."))
        from_status = order.status
        to_status = "reviewer_assigned"
        order._controlled_lifecycle_write({
            "reviewer_user_id": reviewer_user.id,
            "status": to_status,
        })
        self.env["trucalc.order.lifecycle.event"]._log_reviewer_reassignment(
            order, prior, reviewer_user, reason, from_status, to_status, self.env.user,
        )
        return True

    def action_start_review(self):
        self.ensure_one()
        actor = self._require_reviewer_actor()
        self._lock_for_bid_lifecycle()
        order = self.sudo()
        self._require_reviewer_actor()
        if order.reviewer_user_id != actor:
            raise AccessError(_("Only the assigned Reviewer may accept this review."))
        return order._transition_status(
            "reviewer_assigned", "under_review", "review_accepted", actor=actor,
        )

    @api.model
    @api.private
    def _require_reviewer_actor(self):
        actor = self.env.user
        try:
            actor._trucalc_reviewer_identity()
        except ValidationError:
            raise AccessError(_(
                "Only an active TruCalc Reviewer may perform this action."
            ))
        return actor

    @api.private
    def _require_assigned_reviewer(self):
        self.ensure_one()
        actor = self._require_reviewer_actor()
        if self.reviewer_user_id != actor:
            raise AccessError(_("Only the assigned Reviewer may perform this review action."))
        return actor

    def action_open_valuation_revision_wizard(self):
        self.ensure_one()
        self._require_assigned_reviewer()
        if self.status != "under_review" or not self.current_valuation_id:
            raise ValidationError(_("The current Valuation is not eligible for revision."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Request Valuation Revision"),
            "res_model": "trucalc.valuation.revision.wizard",
            "view_mode": "form",
            "view_id": self.env.ref(
                "trucalc_orders.view_trucalc_valuation_revision_wizard_form"
            ).id,
            "target": "new",
            "context": {
                "default_order_id": self.id,
                "default_target_valuation_id": self.current_valuation_id.id,
            },
        }

    def action_request_valuation_revision(self, target, instructions):
        self.ensure_one()
        actor = self._require_assigned_reviewer()
        self._lock_for_bid_lifecycle()
        order = self.sudo()
        self._require_reviewer_actor()
        if order.reviewer_user_id != actor:
            raise AccessError(_("Valuation revision is not authorized."))
        if order.status != "under_review":
            raise ValidationError(_("The current Valuation is not eligible for revision."))
        target = target.sudo().exists()
        if len(target) != 1:
            raise ValidationError(_("The targeted Valuation is no longer available."))
        self.env.cr.execute(
            "SELECT id FROM trucalc_vendor_deliverable WHERE id = %s FOR UPDATE",
            (target.id,),
        )
        target.invalidate_recordset()
        order.invalidate_recordset()
        if target != order.current_valuation_id or self.env[
            "trucalc.order.lifecycle.event"
        ]._valuation_approval(target):
            raise ValidationError(_("The targeted Valuation is no longer eligible for revision."))
        event = self.env["trucalc.order.lifecycle.event"]._request_valuation_revision(
            order, target, instructions, actor,
        )
        order.message_post(body=Markup(_(
            "Valuation revision requested for version %(version)s: %(filename)s"
        )) % {
            "version": target.version,
            "filename": escape(target.filename),
        })
        return event

    def action_approve_valuation(self, target=False):
        self.ensure_one()
        actor = self._require_assigned_reviewer()
        target = (target or self.current_valuation_id).sudo().exists()
        self._lock_for_bid_lifecycle()
        order = self.sudo()
        self._require_reviewer_actor()
        if order.reviewer_user_id != actor:
            raise AccessError(_("Valuation approval is not authorized."))
        if order.status != "under_review":
            raise ValidationError(_("The current Valuation is not eligible for approval."))
        if len(target) != 1:
            raise ValidationError(_("The targeted Valuation is no longer available."))
        self.env.cr.execute(
            "SELECT id FROM trucalc_vendor_deliverable WHERE id = %s FOR UPDATE",
            (target.id,),
        )
        target.invalidate_recordset()
        order.invalidate_recordset()
        Event = self.env["trucalc.order.lifecycle.event"]
        if (
            target.order_id != order or target.artifact_type != "valuation"
            or target.status != "submitted" or not target.is_current
            or target != order.current_valuation_id
        ):
            raise ValidationError(_("Only the exact current Valuation may be approved."))
        if Event._open_valuation_revision_request(target):
            raise ValidationError(_("A Valuation with an open revision request cannot be approved."))
        if Event._valuation_approval(target):
            raise ValidationError(_("This Valuation has already been approved."))
        Event._log_valuation_approval(order, target, actor)
        return True

    @api.private
    def _require_completion_actor(self):
        actor = self.env.user
        if (
            not actor.active or actor.share or actor._trucalc_has_external_role()
            or not (actor.has_group("trucalc_orders.group_trucalc_admin")
                    or actor.has_group("trucalc_orders.group_trucalc_operations"))
            or self.sudo().company_id not in actor.company_ids
        ):
            raise AccessError(_("Order completion is not authorized."))
        self.check_access("read")
        return actor

    @api.private
    def _completion_valuation(self):
        """Called only after the Order lock, with refreshed trusted records."""
        self.ensure_one()
        Event = self.env["trucalc.order.lifecycle.event"].sudo()
        if self.status != "under_review" or Event.search_count([
            ("order_id", "=", self.id), ("event_type", "=", "order_completed"),
        ]):
            raise ValidationError(_("Only an uncompleted Order Under Review may be completed."))
        valuation = self.env["trucalc.vendor.deliverable"].sudo().search([
            ("order_id", "=", self.id), ("artifact_type", "=", "valuation"),
            ("is_current", "=", True),
        ])
        if len(valuation) != 1:
            raise ValidationError(_("Completion requires one current Valuation."))
        self.env.cr.execute(
            "SELECT id FROM trucalc_vendor_deliverable WHERE id = %s FOR UPDATE",
            (valuation.id,),
        )
        valuation.invalidate_recordset()
        self.invalidate_recordset()
        if (valuation.status != "submitted" or not valuation.is_current
                or valuation != self.current_valuation_id
                or valuation.company_id != self.company_id):
            raise ValidationError(_("The current submitted Valuation is no longer valid."))
        approval = Event._valuation_approval(valuation)
        if (
            not approval or approval.order_id != self
            or approval.stable_order_id != self.id
            or approval.company_id != self.company_id
            or not self.reviewer_user_id
            or approval.reviewer_user_id != self.reviewer_user_id
            or approval.actor_id != self.reviewer_user_id
            or approval.from_status != "under_review" or approval.to_status != "under_review"
        ):
            raise ValidationError(_("Completion requires the exact current Reviewer approval."))
        if Event._open_valuation_revision_request(valuation):
            raise ValidationError(_("An open Valuation revision prevents completion."))
        assignment = Event.search([
            ("order_id", "=", self.id),
            ("event_type", "in", ("reviewer_assigned", "reviewer_reassigned")),
        ], order="id desc", limit=1)
        acceptance = Event.search([
            ("order_id", "=", self.id), ("event_type", "=", "review_accepted"),
        ], order="id desc", limit=1)
        if (
            not assignment or not acceptance
            or not assignment.id < acceptance.id < approval.id
            or not assignment.event_at <= acceptance.event_at <= approval.event_at
            or assignment.reviewer_user_id != self.reviewer_user_id
            or acceptance.reviewer_user_id != self.reviewer_user_id
            or acceptance.actor_id != self.reviewer_user_id
            or acceptance.company_id != self.company_id
            or assignment.company_id != self.company_id
            or acceptance.stable_order_id != self.id or assignment.stable_order_id != self.id
            or acceptance.from_status != "reviewer_assigned"
            or acceptance.to_status != "under_review"
        ):
            raise ValidationError(_("Completion requires acceptance and approval in the current review cycle."))
        invoice = self.env["trucalc.vendor.deliverable"].sudo().search([
            ("order_id", "=", self.id), ("artifact_type", "=", "vendor_invoice"),
        ])
        engagement = valuation.engagement_id
        authorization = valuation.authorization_id
        if (
            len(invoice) != 1 or invoice.status != "submitted"
            or invoice.version != 0 or invoice.is_current
            or invoice.company_id != self.company_id
            or invoice.vendor_id != self.assigned_vendor_id
            or invoice.vendor_id != valuation.vendor_id
            or invoice.engagement_id != engagement or invoice.authorization_id != authorization
            or invoice.bidding_round != self.bidding_round
            or valuation.bidding_round != self.bidding_round
            or not engagement.active or engagement.response_state != "accepted"
            or engagement.order_id != self or engagement.company_id != self.company_id
            or engagement.vendor_id != self.assigned_vendor_id
            or engagement.round_number != self.bidding_round
            or engagement.assignment_authorization_id != authorization
            or not authorization.active or authorization.source != "assignment"
            or authorization.order_id != self or authorization.company_id != self.company_id
            or authorization.vendor_id != self.assigned_vendor_id
            or authorization.round_number != self.bidding_round
        ):
            raise ValidationError(_("Completion requires a submitted Vendor Invoice from this Order's accepted engagement."))
        return valuation

    def action_complete_order(self):
        self.ensure_one()
        actor = self._require_completion_actor()
        # A savepoint also guarantees rollback when a trusted caller catches an error.
        with self.env.cr.savepoint():
            order = self.sudo()
            order._lock_for_bid_lifecycle()
            order.invalidate_recordset()
            self._require_completion_actor()
            valuation = order._completion_valuation()
            order.with_context(tracking_disable=True)._controlled_lifecycle_write({
                "status": "completed",
            })
            self.env["trucalc.order.lifecycle.event"]._log_order_completion(
                order, valuation, actor,
            )
        return True

    def action_complete_review(self):
        raise AccessError(_(
            "Review completion is unavailable until the controlled Reviewer workflow."
        ))

    def action_cancelled(self):
        raise AccessError(_(
            "Cancellation is unavailable until the controlled cancellation workflow."
        ))
