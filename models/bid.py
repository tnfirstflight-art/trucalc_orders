from odoo import models, fields
from odoo.exceptions import ValidationError


class TruCalcBid(models.Model):
    _name = "trucalc.bid"
    _description = "Vendor Bid"
    _order = "create_date desc"

    invitation_id = fields.Many2one(
        "trucalc.bid.invitation",
        string="Invitation",
        required=True,
        index=True,
        ondelete="restrict",
    )

    order_id = fields.Many2one(
        "trucalc.order",
        string="Order",
        related="invitation_id.order_id",
        store=True,
        readonly=True,
        required=True,
        index=True,
    )

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        related="invitation_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )

    vendor_id = fields.Many2one(
        "trucalc.vendor",
        string="Vendor",
        related="invitation_id.vendor_id",
        store=True,
        readonly=True,
        required=True,
        index=True,
    )

    option_name = fields.Char(
        string="Option Name",
        required=True,
    )

    bid_amount = fields.Float(
        string="Bid Amount",
        required=True,
    )

    turn_time_days = fields.Integer(
        string="Turn Time (Days)",
    )

    notes = fields.Text(
        string="Notes",
    )

    status = fields.Selection(
        [
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("selected", "Selected"),
            ("not_selected", "Not Selected"),
            ("disqualified", "Disqualified"),
        ],
        string="Status",
        default="submitted",
    )

    def action_select_bid(self):
        self.ensure_one()

        if self.order_id.status != "bid_requested":
            raise ValidationError(
                "Bidding must be reopened before selecting a different vendor."
            )

        self.order_id.bid_ids.filtered(
            lambda b: b.id != self.id
        ).write(
            {"status": "rejected"}
        )

        self.write(
            {"status": "selected"}
        )

        self.order_id.write(
            {
                "assigned_vendor_id": self.vendor_id.id,
                "vendor_fee": self.bid_amount,
                "status": "assigned",
            }
        )

        self.order_id.message_post(
            body=(
                f"Winning bid selected:<br/>"
                f"Vendor: {self.vendor_id.name}<br/>"
                f"Bid Amount: ${self.bid_amount:,.2f}"
            )
        )
