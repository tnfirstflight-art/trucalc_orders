from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


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
        [
            ("evaluation", "Evaluation"),
            ("appraisal", "Appraisal"),
            ("review", "Review"),
            ("environmental", "Environmental"),
        ],
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
    )

    due_date = fields.Date(
        string="Due Date",
        tracking=True,
    )

    status = fields.Selection(
        [
            ("new", "New"),
            ("bid_requested", "Bid Requested"),
            ("assigned", "Assigned"),
            ("report_received", "Report Received"),
            ("reviewer_assigned", "Reviewer Assigned"),
            ("under_review", "Under Review"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
        default="new",
        tracking=True,
    )

    notes = fields.Text(
        string="Notes",
    )

    reviewer_id = fields.Many2one(
        "trucalc.vendor",
        string="Reviewer",
        tracking=True,
        domain="[('vendor_type', '=', 'reviewer')]",
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

    @api.model_create_multi
    def create(self, vals_list):
        user = self.env.user
        bank_company = False
        if user._trucalc_has_bank_role():
            bank_company = user._trucalc_bank_identity()
        for vals in vals_list:
            if (
                vals.get("status", "new") != "new"
                or vals.get("bidding_round", 0) != 0
                or vals.get("assigned_vendor_id")
                or vals.get("vendor_fee")
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
            return super().create(vals_list)

        trusted_context = dict(self.env.context)
        trusted_context["allowed_company_ids"] = []
        for field in ("company_id", "requestor_company_id", "requestor_id"):
            trusted_context.pop("default_%s" % field, None)
        trusted_model = self.with_context(trusted_context)
        return super(EvaluationOrder, trusted_model).create(vals_list)

    def write(self, vals):
        if self.env.user._trucalc_has_bank_role():
            self.env.user._trucalc_bank_identity()
            if {"company_id", "requestor_company_id", "requestor_id"} & vals.keys():
                raise AccessError(_("TruCalc bank order ownership is immutable."))
        protected = {"bidding_round", "assigned_vendor_id", "vendor_fee"}
        if protected.intersection(vals):
            raise AccessError(_("Order bid lifecycle fields require an explicit action."))
        if "status" in vals:
            protected_transitions = {
                ("new", "bid_requested"),
                ("assigned", "bid_requested"),
                ("bid_requested", "assigned"),
            }
            if any((order.status, vals["status"]) in protected_transitions for order in self):
                raise AccessError(_("This order status transition requires an explicit action."))
        return super().write(vals)

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

    @api.private
    def _lock_for_bid_lifecycle(self):
        self.ensure_one()
        self.flush_recordset(
            ["status", "bidding_round", "assigned_vendor_id", "vendor_fee"]
        )
        self.env.cr.execute(
            "SELECT id FROM trucalc_order WHERE id = %s FOR UPDATE", (self.id,)
        )
        self.invalidate_recordset(
            ["status", "bidding_round", "assigned_vendor_id", "vendor_fee"]
        )

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

    def action_bid_requested(self):
        self._require_bid_manager()
        self.ensure_one()
        self._lock_for_bid_lifecycle()
        if self.status != "new" or self.bidding_round != 0 or self.invitation_ids:
            raise ValidationError(_("Only a new order without bid history may start bidding."))
        self._controlled_lifecycle_write({"status": "bid_requested", "bidding_round": 1})
        self.env["trucalc.bid.audit"]._log_event(
            "bidding_started", self,
            old_values={"status": "new", "bidding_round": 0},
            new_values={"status": "bid_requested", "bidding_round": 1},
        )
        return True

    def action_assigned(self):
        raise AccessError(_("An order may only be assigned by selecting a submitted bid."))

    def action_report_received(self):
        self.status = "report_received"

    def action_reopen_bidding(self):
        self._require_bid_manager()
        self.ensure_one()
        self._lock_for_bid_lifecycle()
        if self.status != "assigned":
            raise ValidationError(_("Only an assigned order may reopen bidding."))
        old_round = self.bidding_round
        old_vendor = self.assigned_vendor_id.id
        old_fee = self.vendor_fee
        self._controlled_lifecycle_write({
            "status": "bid_requested",
            "bidding_round": old_round + 1,
            "assigned_vendor_id": False,
            "vendor_fee": 0.0,
        })
        self.env["trucalc.bid.audit"]._log_event(
            "bidding_reopened", self,
            old_values={"status": "assigned", "bidding_round": old_round,
                        "assigned_vendor_id": old_vendor, "vendor_fee": old_fee},
            new_values={"status": "bid_requested", "bidding_round": old_round + 1,
                        "assigned_vendor_id": False, "vendor_fee": 0.0},
        )
        return True

    def action_assign_reviewer(self):
        self.status = "reviewer_assigned"

    def action_start_review(self):
        self.status = "under_review"

    def action_complete_review(self):
        self.status = "completed"

    def action_cancelled(self):
        self.status = "cancelled"
