from odoo import models, fields, api


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

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("order_number", "New") == "New":
                vals["order_number"] = self.env["ir.sequence"].next_by_code(
                    "trucalc.order"
                ) or "New"
        return super().create(vals_list)

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
        self.status = "bid_requested"

    def action_assigned(self):
        self.status = "assigned"

    def action_report_received(self):
        self.status = "report_received"

    def action_reopen_bidding(self):
        self.status = "bid_requested"
        self.assigned_vendor_id = False

    def action_assign_reviewer(self):
        self.status = "reviewer_assigned"

    def action_start_review(self):
        self.status = "under_review"

    def action_complete_review(self):
        self.status = "completed"

    def action_cancelled(self):
        self.status = "cancelled"