import base64
from datetime import timedelta

from odoo import Command, fields
from odoo.tests import TransactionCase

from ..models.notification_delivery import (
    PARAM_ACTIVATION,
    PARAM_ALLOWED_HOST,
    PARAM_CAPTURE,
    PARAM_DISPATCH,
    PARAM_FROM,
    PARAM_REPLY_TO,
)


PDF = base64.b64encode(b"%PDF-1.7\nPhase A synthetic fixture\n%%EOF")


class NotificationTestCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Companies = cls.env["res.company"].with_context(
            trucalc_test_bank_fixture=True,
        )
        cls.company = Companies.create({
            "name": "Phase A Synthetic Bank",
            "trucalc_is_bank": True,
            "trucalc_bank_active": True,
        })
        cls.other_company = Companies.create({
            "name": "Phase A Other Synthetic Bank",
            "trucalc_is_bank": True,
            "trucalc_bank_active": True,
        })
        cls.admin = cls._internal_user(
            "phase-a-admin", "group_trucalc_admin", cls.company,
        )
        cls.ops = cls._internal_user(
            "phase-a-ops", "group_trucalc_operations", cls.company,
        )
        cls.foreign_admin = cls._internal_user(
            "phase-a-foreign-admin", "group_trucalc_admin", cls.other_company,
        )
        cls.requestor = cls._bank_user(
            "phase-a-requestor@example.test", "group_bank_requestor", cls.company,
        )
        cls.bank_admin = cls._bank_user(
            "phase-a-bank-admin@example.test", "group_bank_admin", cls.company,
        )
        reviewer_company = cls.env["res.company"].create({
            "name": "Phase A Reviewer Company",
        })
        cls.reviewer = cls._internal_user(
            "phase-a-reviewer", "group_trucalc_operations", reviewer_company,
            extra_groups=["group_trucalc_reviewer"],
        )
        cls.vendor = cls.env["trucalc.vendor"].create({
            "name": "Phase A Synthetic Vendor",
        })
        cls.env["trucalc.vendor.fee"].create({
            "vendor_id": cls.vendor.id,
            "service_type": "evaluation",
            "fee": 500,
        })
        cls.vendor_user = cls.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Phase A Vendor",
            "login": "phase-a-vendor@example.test",
            "email": "phase-a-vendor@example.test",
            "company_id": cls.env.company.id,
            "company_ids": [Command.set(cls.env.company.ids)],
            "group_ids": [Command.set([
                cls.env.ref("trucalc_orders.group_vendor_portal").id,
            ])],
            "trucalc_vendor_id": cls.vendor.id,
        })
        state = cls.env["res.country.state"].create({
            "name": "Phase A Synthetic State",
            "code": "PZ",
            "country_id": cls.env.ref("base.us").id,
        })
        cls.area = cls.env["trucalc.service.area"].with_user(cls.admin).create({
            "state_id": state.id,
            "county": "Phase A Synthetic County",
            "service_type": "evaluation",
            "base_fee": 500,
        })

    @classmethod
    def _internal_user(cls, login, group, company, extra_groups=None):
        groups = [group] + list(extra_groups or [])
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login,
            "login": "%s@example.test" % login,
            "email": "%s@example.test" % login,
            "company_id": company.id,
            "company_ids": [Command.set(company.ids)],
            "group_ids": [Command.set([
                cls.env.ref("trucalc_orders.%s" % item).id for item in groups
            ])],
        })

    @classmethod
    def _bank_user(cls, login, group, company):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login,
            "login": login,
            "email": login,
            "company_id": company.id,
            "company_ids": [Command.set(company.ids)],
            "group_ids": [Command.set([
                cls.env.ref("trucalc_orders.%s" % group).id,
            ])],
            "trucalc_bank_company_id": company.id,
        })

    def setUp(self):
        super().setUp()
        self.params = self.env["ir.config_parameter"].sudo()
        for key in (
            PARAM_CAPTURE, PARAM_DISPATCH, PARAM_ACTIVATION, PARAM_FROM,
            PARAM_REPLY_TO, PARAM_ALLOWED_HOST,
        ):
            self.params.set_param(key, False)

    def _enable_capture(self, activation_at=None):
        activation_at = activation_at or (
            fields.Datetime.now() - timedelta(minutes=1)
        )
        self.params.set_param(PARAM_CAPTURE, "true")
        self.params.set_param(
            PARAM_ACTIVATION, fields.Datetime.to_string(activation_at),
        )

    def _configure_rendering(self):
        self.params.set_param("web.base.url", "https://trucalc.example.test")
        self.params.set_param(PARAM_ALLOWED_HOST, "trucalc.example.test")
        self.params.set_param(PARAM_FROM, "notices@example.test")
        self.params.set_param(PARAM_REPLY_TO, "support@example.test")

    def _bank_values(self):
        return {
            "borrower": "Synthetic Borrower",
            "property_address": "100 Synthetic Test Way",
            "city": "Memphis",
            "zip_code": "38103",
            "loan_number": "SYNTHETIC-LOAN",
            "loan_purpose": "new_loan",
            "service_type": "evaluation",
            "property_type": "single_family",
            "due_date": fields.Date.to_string(fields.Date.today()),
            "inspection_contact_name": "Synthetic Contact",
            "inspection_contact_phone": "9015550100",
            "inspection_contact_email": "contact@example.test",
            "notes": "Synthetic notes that must never enter notification mail.",
            "service_area_id": str(self.area.id),
            "service_state_id": str(self.area.state_id.id),
            "service_county": self.area.county,
        }

    def _ready_order(self, requestor=None):
        requestor = requestor or self.requestor
        Order = self.env["trucalc.order"].with_user(requestor)
        values = self._bank_values()
        order = Order._create_bank_draft(values, requestor)
        Order._send_bank_draft(order, values, requestor)
        order.with_user(self.admin).action_accept_request()
        order.with_user(self.admin).action_request_vendor_bids(
            self.vendor, fields.Datetime.now() + timedelta(days=2),
        )
        invitation = order.invitation_ids.filtered(
            lambda item: item.vendor_id == self.vendor
        )
        bid = invitation.with_user(self.vendor_user).action_vendor_submit_response(
            "standard_terms_accepted"
        )
        bid.with_user(self.ops)._action_confirm_engagement()
        self.env.flush_all()
        self.env["trucalc.vendor.order"].invalidate_model()
        projection = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user
        ).search([("order_number", "=", order.order_number)])
        projection.action_vendor_accept_engagement()
        authorization = order.vendor_authorization_ids.filtered(
            lambda item: item.active and item.source == "assignment"
        )
        valuation = self.env["trucalc.vendor.deliverable"].with_user(
            self.vendor_user
        )._submit(
            authorization, self.vendor_user, "valuation", "Approved.pdf", PDF,
        )
        self.env["trucalc.vendor.deliverable"].with_user(
            self.vendor_user
        )._submit(
            authorization, self.vendor_user, "vendor_invoice", "Invoice.pdf", PDF,
        )
        order.with_user(self.admin).action_assign_reviewer(
            self.reviewer, fields.Date.add(fields.Date.today(), days=5),
        )
        order.with_user(self.reviewer).action_start_review()
        order.with_user(self.reviewer).action_approve_valuation(valuation)
        return order, valuation

    def _complete_and_delivery(self, requestor=None):
        self._enable_capture()
        order, valuation = self._ready_order(requestor=requestor)
        order.with_user(self.admin).action_complete_order()
        delivery = self.env["trucalc.notification.delivery"].sudo().search([
            ("order_id", "=", order.id),
        ])
        self.assertEqual(len(delivery), 1)
        return order, valuation, delivery
