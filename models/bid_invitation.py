from odoo import fields, models


class TruCalcBidInvitation(models.Model):
    _name = "trucalc.bid.invitation"
    _description = "Vendor Bid Invitation"
    _order = "order_id, round_number desc, vendor_id"

    order_id = fields.Many2one(
        "trucalc.order",
        string="Order",
        required=True,
        index=True,
        ondelete="cascade",
    )

    vendor_id = fields.Many2one(
        "trucalc.vendor",
        string="Vendor",
        required=True,
        index=True,
        ondelete="restrict",
    )

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        related="order_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )

    round_number = fields.Integer(
        string="Bidding Round",
        required=True,
        default=1,
    )

    state = fields.Selection(
        [
            ("invited", "Invited"),
            ("declined", "Declined"),
            ("revoked", "Revoked"),
            ("expired", "Expired"),
            ("closed", "Closed"),
        ],
        string="State",
        required=True,
        default="invited",
    )

    response_deadline = fields.Datetime(
        string="Response Deadline",
    )

    invited_by = fields.Many2one(
        "res.users",
        string="Invited By",
        default=lambda self: self.env.user,
        readonly=True,
    )

    invited_at = fields.Datetime(
        string="Invited At",
        default=fields.Datetime.now,
        readonly=True,
    )

    declined_at = fields.Datetime(
        string="Declined At",
        readonly=True,
    )

    revoked_at = fields.Datetime(
        string="Revoked At",
        readonly=True,
    )

    is_legacy_reconstructed = fields.Boolean(
        string="Legacy Reconstructed",
        default=False,
        readonly=True,
    )

    bid_ids = fields.One2many(
        "trucalc.bid",
        "invitation_id",
        string="Bid Options",
    )

    _order_vendor_round_unique = models.Constraint(
        "UNIQUE(order_id, vendor_id, round_number)",
        "A vendor may only have one invitation per order and bidding round.",
    )

    _round_number_positive = models.Constraint(
        "CHECK(round_number > 0)",
        "The bidding round must be greater than zero.",
    )
