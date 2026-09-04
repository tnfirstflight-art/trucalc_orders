from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, TransactionCase, tagged
from lxml import etree


@tagged("post_install", "-at_install", "trucalc_pricing")
class TestPricingArchitecture(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls._user("4e0-admin", "group_trucalc_admin")
        cls.ops = cls._user("4e0-ops", "group_trucalc_operations")
        cls.reviewer = cls._user("4e0-reviewer", "group_trucalc_reviewer")
        cls.bank_a = cls.env["res.company"].create({"name": "4E0 Bank A"})
        cls.bank_b = cls.env["res.company"].create({"name": "4E0 Bank B"})
        cls.admin.write({
            "company_ids": [Command.link(cls.bank_a.id), Command.link(cls.bank_b.id)],
        })
        cls.ops.sudo().write({
            "company_ids": [Command.link(cls.bank_a.id), Command.link(cls.bank_b.id)],
        })
        cls.bank_user = cls._user(
            "4e0-bank", "group_bank_requestor", bank=cls.bank_a,
        )
        cls.vendor = cls.env["trucalc.vendor"].create({"name": "4E0 Vendor"})
        cls.vendor_user = cls._user(
            "4e0-vendor", "group_vendor_portal", vendor=cls.vendor,
        )
        country = cls.env.ref("base.us")
        cls.state = cls.env["res.country.state"].create({
            "name": "4E0 State", "code": "E0", "country_id": country.id,
        })
        cls.area = cls.env["trucalc.service.area"].with_user(cls.admin).create({
            "state_id": cls.state.id,
            "county": "Pricing County",
            "service_type": "evaluation",
            "base_fee": 500,
        })

    @classmethod
    def _user(cls, login, group, bank=False, vendor=False):
        groups = [group]
        if group == "group_trucalc_reviewer":
            groups.append("group_trucalc_operations")
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login,
            "login": login,
            "email": f"{login}@example.test",
            "group_ids": [Command.set([
                cls.env.ref(f"trucalc_orders.{name}").id for name in groups
            ])],
            "trucalc_bank_company_id": bank.id if bank else False,
            "trucalc_vendor_id": vendor.id if vendor else False,
        })

    def _schedule(self, bank=None, fee=425):
        return self.env["trucalc.negotiated.fee"].with_user(self.admin).create({
            "bank_id": (bank or self.bank_a).id,
            "service_area_id": self.area.id,
            "negotiated_fee": fee,
        })

    def _bank_values(self):
        return {
            "borrower": "Pricing Borrower",
            "property_address": "1 Pricing Way",
            "city": "Pricing City",
            "zip_code": "38600",
            "loan_number": "PRICE-1",
            "service_type": "evaluation",
            "property_type": "commercial",
            "due_date": fields.Date.to_string(
                fields.Date.add(fields.Date.today(), days=10)
            ),
            "inspection_contact_name": "Pricing Contact",
            "inspection_contact_phone": "9015550100",
            "inspection_contact_email": "pricing@example.test",
            "notes": "",
            "service_area_id": str(self.area.id),
            "service_state_id": str(self.state.id),
            "service_county": self.area.county,
        }

    def test_resolver_is_deterministic_and_bank_isolated(self):
        schedule = self._schedule()
        resolved = self.env["trucalc.order"]._resolve_bank_fee(
            self.bank_a, self.area,
        )
        self.assertEqual(
            (resolved["agreed_fee"], resolved["fee_source"],
             resolved["negotiated_fee_id"]),
            (425, "negotiated", schedule.id),
        )
        fallback = self.env["trucalc.order"]._resolve_bank_fee(
            self.bank_b, self.area,
        )
        self.assertEqual(
            (fallback["agreed_fee"], fallback["fee_source"],
             fallback["negotiated_fee_id"]),
            (500, "base", False),
        )
        schedule.with_user(self.admin).active = False
        self.assertEqual(
            self.env["trucalc.order"]._resolve_bank_fee(
                self.bank_a, self.area,
            )["fee_source"],
            "base",
        )
        self.env.cr.execute(
            "UPDATE trucalc_service_area SET base_fee = NULL WHERE id = %s",
            (self.area.id,),
        )
        self.area.invalidate_recordset(["base_fee"])
        with self.assertRaises(ValidationError):
            self.env["trucalc.order"]._resolve_bank_fee(self.bank_a, self.area)
        self.area.with_user(self.admin).active = False
        with self.assertRaises(ValidationError):
            self.env["trucalc.order"]._resolve_bank_fee(self.bank_b, self.area)

    def test_schedule_constraints_archive_and_security(self):
        schedule = self._schedule()
        self.assertEqual(schedule.currency_id, self.env.ref("base.USD"))
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._schedule(bank=self.bank_b, fee=-1)
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._schedule(fee=450)
        schedule.with_user(self.admin).active = False
        self.assertFalse(schedule.active)
        with self.assertRaises(AccessError):
            schedule.with_user(self.admin).unlink()
        for user in (self.ops, self.reviewer, self.bank_user, self.vendor_user):
            model = self.env["trucalc.negotiated.fee"].with_user(user)
            for operation in ("read", "create", "write", "unlink"):
                self.assertFalse(model.has_access(operation))

    def test_schedule_modal_create_and_inline_edit_contract(self):
        list_view = self.env.ref(
            "trucalc_orders.view_trucalc_negotiated_fee_list"
        )
        list_node = etree.fromstring(list_view.arch.encode()).xpath("//list")[0]
        self.assertEqual(
            (list_node.get("create"), list_node.get("delete"),
             list_node.get("editable")),
            ("false", "false", "bottom"),
        )
        button = list_node.xpath("./header/button[@string='New']")
        self.assertEqual(len(button), 1)
        new_action = self.env.ref(
            "trucalc_orders.action_trucalc_negotiated_fee_new"
        )
        self.assertEqual(
            (new_action.view_mode, new_action.view_id,
             new_action.target, new_action.context),
            ("form", self.env.ref(
                "trucalc_orders.view_trucalc_negotiated_fee_form"
            ), "new", "{'form_view_initial_mode': 'edit'}"),
        )
        action = self.env.ref("trucalc_orders.action_trucalc_negotiated_fees")
        self.assertEqual(
            (action.view_mode, action.view_id), ("list", list_view),
        )
        self.assertEqual(
            [field.get("name") for field in list_node.xpath("./field")
             if field.get("readonly") != "1"],
            ["negotiated_fee", "active"],
        )

    def test_bank_send_snapshots_provenance_and_never_reprices(self):
        schedule = self._schedule()
        model = self.env["trucalc.order"].with_user(self.bank_user)
        draft = model._create_bank_draft(self._bank_values(), self.bank_user)
        self.assertFalse(draft.fee_locked_at)
        self.assertFalse(draft.fee_source)
        sent = model._send_bank_draft(draft, self._bank_values(), self.bank_user)
        self.assertEqual(
            (sent.agreed_fee, sent.fee_source, sent.negotiated_fee_id,
             sent.fee_currency_id),
            (425, "negotiated", schedule, self.env.ref("base.USD")),
        )
        self.assertTrue(sent.fee_locked_at)
        self.assertFalse(sent.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "pricing_locked"
        ))
        event = sent.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "bank_request_sent"
        )
        self.assertEqual(
            (event.agreed_fee, event.fee_source, event.service_area_id,
             event.negotiated_fee_id, event.fee_currency_id,
             event.fee_locked_at),
            (sent.agreed_fee, sent.fee_source, sent.service_area_id,
             sent.negotiated_fee_id, sent.fee_currency_id,
             sent.fee_locked_at),
        )
        schedule.with_user(self.admin).negotiated_fee = 450
        self.assertEqual(sent.agreed_fee, 425)
        for values in (
            {"agreed_fee": 1}, {"fee_source": "base"},
            {"negotiated_fee_id": False}, {"fee_locked_at": fields.Datetime.now()},
            {"fee_currency_id": self.env.ref("base.USD").id},
            {"service_area_id": self.area.id}, {"service_type": "appraisal"},
        ):
            with self.assertRaises(AccessError):
                sent.with_user(self.admin).write(values)

    def test_internal_submit_uses_current_resolver_and_fee_override_is_isolated(self):
        schedule = self._schedule()
        order = self.env["trucalc.order"].with_user(self.admin).with_context(
            trucalc_internal_draft_intake=True,
        ).create({
            "borrower": "Internal Pricing",
            "property_address": "2 Pricing Way",
            "company_id": self.bank_a.id,
            "pricing_state_id": self.state.id,
            "pricing_county_area_id": self.area.id,
            "service_area_id": self.area.id,
            "service_type": "evaluation",
            "state": "E0",
            "county": self.area.county,
            "due_date": fields.Date.add(fields.Date.today(), days=10),
        })
        order.with_user(self.admin).write({
            "review_fee": 100, "fee_override": True,
        })
        self.assertEqual(
            self.env["trucalc.order"]._resolve_bank_fee(
                self.bank_a, self.area,
            )["agreed_fee"],
            425,
        )
        schedule.with_user(self.admin).negotiated_fee = 450
        order.with_user(self.admin).action_submit_internal_draft()
        self.assertEqual(
            (order.status, order.agreed_fee, order.fee_source,
             order.negotiated_fee_id),
            ("new", 450, "negotiated", schedule),
        )
        self.assertEqual((order.review_fee, order.fee_override), (100, True))
        event = order.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "internal_request_submitted"
        )
        self.assertEqual(len(event), 1)
        self.assertEqual(
            (event.from_status, event.to_status, event.stable_order_id,
             event.company_id, event.actor_id, event.agreed_fee, event.fee_source,
             event.service_area_id, event.negotiated_fee_id,
             event.fee_currency_id, event.event_at),
            ("draft", "new", order.id, order.company_id,
             self.admin, order.agreed_fee, order.fee_source,
             order.service_area_id, order.negotiated_fee_id,
             order.fee_currency_id, order.fee_locked_at),
        )
        self.assertTrue(order.order_date)
        with self.assertRaises(ValidationError):
            order.with_user(self.admin).action_submit_internal_draft()
        self.assertEqual(len(order.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "internal_request_submitted"
        )), 1)

    def test_acceptance_and_legacy_lifecycle_do_not_require_pricing(self):
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "Legacy Internal",
            "property_address": "3 Legacy Way",
            "due_date": fields.Date.add(fields.Date.today(), days=10),
        })
        order.with_user(self.admin).action_accept_request()
        self.assertEqual(order.status, "accepted")
        self.assertFalse(order.fee_locked_at)
        self.assertFalse(order.service_area_id)

    def test_incomplete_internal_order_is_unpriced_and_fields_are_optional(self):
        order = self.env["trucalc.order"].with_user(self.admin).with_context(
            trucalc_internal_draft_intake=True,
        ).create({})
        self.assertEqual(order.status, "draft")
        self.assertFalse(order.borrower)
        self.assertFalse(order.property_address)
        self.assertFalse(order.due_date)
        self.assertFalse(order.order_date)
        self.assertFalse(order.pricing_state_id)
        self.assertFalse(order.pricing_county_area_id)
        self.assertFalse(order.service_type)
        self.assertFalse(order.service_area_id)
        self.assertFalse(order.agreed_fee)
        self.assertFalse(order.fee_source)
        self.assertFalse(order.fee_locked_at)
        self.assertFalse(order.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "internal_request_submitted"
        ))
        arch = etree.fromstring(self.env.ref(
            "trucalc_orders.view_trucalc_order_form"
        ).arch.encode())
        details = arch.xpath(
            "//group[@string='Property / Service Details']"
        )[0]
        for field_name in (
            "pricing_state_id", "pricing_county_area_id", "service_area_id",
        ):
            field = details.xpath(f"./field[@name='{field_name}']")[0]
            self.assertEqual(field.get("required"), "0")
        agreed = arch.xpath(
            "//group[@string='Pricing Summary']/field[@name='agreed_fee']"
        )[0]
        self.assertEqual(
            agreed.getparent().get("invisible"), "not fee_locked_at"
        )
        for field_name in ("borrower", "property_address"):
            self.assertEqual(
                arch.xpath(f"//field[@name='{field_name}' and not(@invisible='1')]")[0].get("required"),
                "not is_internal_draft",
            )
        due = arch.xpath(
            "//group[@string='Order / Request Information']/field[@name='due_date']"
        )[0]
        self.assertEqual(due.get("required"), "not is_internal_draft")

    def test_internal_upper_form_layout_has_one_controlled_selector_set(self):
        arch = etree.fromstring(self.env.ref(
            "trucalc_orders.view_trucalc_order_form"
        ).arch.encode())
        self.assertFalse(arch.xpath("//group[@string='Pricing Selection']"))
        details = arch.xpath(
            "//group[@string='Property / Service Details']"
        )[0]
        self.assertEqual(
            [field.get("name") for field in details.xpath("./field")],
            [
                "property_address", "city", "pricing_state_id",
                "pricing_county_area_id", "zip_code", "service_area_id",
                "property_type",
            ],
        )
        request = arch.xpath(
            "//group[@string='Order / Request Information']"
        )[0]
        self.assertEqual(
            [(field.get("name"), field.get("string"))
             for field in request.xpath("./field")],
            [
                ("company_id", "Bank"),
                ("requestor_id", "Requester"),
                ("order_date_display", "Order Date"),
                ("loan_number", "Loan Number"),
                ("borrower", "Borrower"),
                ("due_date", "Due Date"),
                ("status", "Status"),
            ],
        )
        for label, field_name in (
            ("State", "pricing_state_id"),
            ("County", "pricing_county_area_id"),
            ("Service Type", "service_area_id"),
        ):
            self.assertEqual(len(arch.xpath(
                f"//field[@string='{label}' and not(@invisible='1')]"
            )), 1)
            self.assertEqual(
                details.xpath(f"./field[@string='{label}']")[0].get("name"),
                field_name,
            )
        self.assertEqual(
            details.xpath("./field[@name='pricing_state_id']")[0].get("context"),
            "{'trucalc_state_code_only': True}",
        )
        self.assertEqual(
            details.xpath(
                "./field[@name='pricing_county_area_id']"
            )[0].get("context"),
            "{'trucalc_service_area_display': 'county'}",
        )
        self.assertEqual(
            details.xpath("./field[@name='service_area_id']")[0].get("context"),
            "{'trucalc_service_area_display': 'service_type'}",
        )
        for legacy in ("state", "county", "service_type"):
            self.assertEqual(len(arch.xpath(
                f"//sheet/field[@name='{legacy}'][@invisible='1']"
            )), 1)
        self.assertEqual(len(arch.xpath(
            "//group[@string='Pricing Summary'][@invisible='not fee_locked_at']"
        )), 1)
        for lower_surface in (
            "deliverables_display", "valuation_display", "vendor_invoice_display",
        ):
            self.assertEqual(len(arch.xpath(
                f"//div[@name='{lower_surface}']"
            )), 1)
        for page in ("Notes", "Documents", "Vendor Responses"):
            self.assertEqual(len(arch.xpath(f"//page[@string='{page}']")), 1)
        sheet = arch.xpath("//sheet")[0]
        inspection = arch.xpath("//group[@string='Inspection Contact']")[0]
        pricing = arch.xpath("//group[@string='Pricing Summary']")[0]
        workflow = arch.xpath("//group[@string='Workflow Information']")[0]
        self.assertLess(sheet.index(inspection), sheet.index(pricing))
        self.assertLess(sheet.index(pricing), sheet.index(workflow))
        self.assertEqual(
            [(field.get("name"), field.get("string"))
             for field in inspection.xpath("./field")],
            [
                ("inspection_contact_name", "Name"),
                ("inspection_contact_phone_display", "Phone"),
                ("inspection_contact_email", "Email"),
            ],
        )
        self.assertEqual(
            [(button.get("name"), button.get("string"))
            for button in arch.xpath("//header/button")],
            [
                (None, "Save Draft"),
                ("action_submit_internal_draft", "Submit"),
                ("action_accept_request", "Accept"),
                ("action_open_decline_wizard", "Decline"),
                ("action_open_request_bids_wizard", "Request Bids"),
                ("action_open_manage_bid_requests_wizard", "Manage Bid Requests"),
                ("action_open_extend_bid_deadline_wizard", "Extend Bid Deadline"),
                ("action_reopen_bidding", "Reopen Bidding"),
                ("action_open_engagement_decision_wizard", "Review Delivery Change"),
                ("action_open_reviewer_assignment_wizard", "Assign Reviewer"),
                ("action_open_reviewer_assignment_wizard", "Reassign Reviewer"),
                ("action_start_review", "Accept Review"),
                ("action_open_valuation_revision_wizard", "Request Revision"),
                ("action_approve_valuation", "Approve Valuation"),
                ("action_complete_order", "Complete Order"),
            ],
        )
        save_draft = arch.xpath("//header/button[@string='Save Draft']")[0]
        self.assertEqual(save_draft.get("special"), "save")
        self.assertFalse(save_draft.get("name"))
        self.assertFalse(save_draft.get("type"))
        self.assertEqual(save_draft.get("invisible"), "not is_internal_draft")
        submit = arch.xpath(
            "//header/button[@name='action_submit_internal_draft']"
        )[0]
        self.assertEqual(submit.get("invisible"), "not id or not is_internal_draft")

    def test_internal_draft_save_security_and_atomic_submit(self):
        model = self.env["trucalc.order"].with_user(self.admin).with_context(
            trucalc_internal_draft_intake=True,
            default_company_id=False,
        )
        draft = model.create({})
        self.assertEqual(draft.status, "draft")
        self.assertFalse(draft.company_id)
        self.assertFalse(draft.order_date)
        self.assertEqual(draft.requestor_id, self.admin)
        self.assertEqual(draft.requestor_company_id, self.env.company)
        self.assertTrue(draft.is_internal_draft)
        self.assertNotIn(self.env.company, draft.available_internal_bank_ids)
        self.assertIn(self.bank_a, draft.available_internal_bank_ids)
        self.assertTrue(draft.with_user(self.ops).has_access("write"))
        self.assertFalse(draft.with_user(self.bank_user).has_access("read"))
        self.assertFalse(draft.with_user(self.vendor_user).has_access("read"))
        self.assertFalse(draft.agreed_fee)
        self.assertFalse(draft.lifecycle_event_ids.filtered(
            lambda event: event.event_type in (
                "internal_request_submitted", "pricing_locked", "bank_request_sent"
            )
        ))
        with self.assertRaises(ValidationError):
            draft.with_user(self.admin).company_id = self.env.company
        with self.assertRaises(ValidationError):
            draft.with_user(self.admin).action_submit_internal_draft()
        self.assertEqual(draft.status, "draft")
        self.assertFalse(draft.order_date)
        self.assertFalse(draft.fee_locked_at)

        draft.with_user(self.admin).write({
            "company_id": self.bank_a.id,
            "borrower": "Internal Draft",
            "property_address": "3B Draft Way",
            "due_date": fields.Date.add(fields.Date.today(), days=10),
            "pricing_state_id": self.state.id,
            "pricing_county_area_id": self.area.id,
            "service_area_id": self.area.id,
            "service_type": "evaluation",
            "state": "E0",
            "county": self.area.county,
        })
        self.assertFalse(draft.with_user(self.bank_user).has_access("read"))
        self.assertFalse(draft.agreed_fee)
        draft.with_user(self.admin).action_submit_internal_draft()
        self.assertEqual(draft.status, "new")
        self.assertTrue(draft.order_date)
        self.assertEqual((draft.agreed_fee, draft.fee_source), (500, "base"))
        with self.assertRaises(AccessError):
            draft.with_user(self.admin).company_id = self.bank_b
        self.assertFalse(draft.lifecycle_event_ids.filtered(
            lambda event: event.event_type == "bank_request_sent"
        ))
        self.assertEqual(len(draft.lifecycle_event_ids.filtered(
            lambda event: event.event_type == "internal_request_submitted"
        )), 1)
        self.assertFalse(draft.lifecycle_event_ids.filtered(
            lambda event: event.event_type == "pricing_locked"
        ))

        orders_action = self.env.ref("trucalc_orders.action_trucalc_orders")
        self.assertEqual(
            orders_action.context,
            "{'default_status': 'draft', 'default_company_id': False, "
            "'trucalc_internal_draft_intake': True}",
        )

    def test_internal_selector_options_and_exact_resolution(self):
        appraisal = self.env["trucalc.service.area"].with_user(self.admin).create({
            "state_id": self.state.id,
            "county": self.area.county,
            "service_type": "appraisal",
            "base_fee": 600,
        })
        other_state = self.env["res.country.state"].create({
            "name": "4E0 Other State", "code": "EO",
            "country_id": self.env.ref("base.us").id,
        })
        other_area = self.env["trucalc.service.area"].with_user(self.admin).create({
            "state_id": other_state.id,
            "county": "Other County",
            "service_type": "evaluation",
            "base_fee": 700,
        })
        other_county = self.env["trucalc.service.area"].with_user(self.admin).create({
            "state_id": self.state.id,
            "county": "Second County",
            "service_type": "evaluation",
            "base_fee": 650,
        })
        inactive = self.env["trucalc.service.area"].with_user(self.admin).create({
            "state_id": self.state.id,
            "county": "Inactive County",
            "service_type": "appraisal",
            "base_fee": 600,
            "active": False,
        })
        form = Form(self.env["trucalc.order"].with_user(self.admin))
        form.borrower = "Selector"
        form.property_address = "4 Selector Way"
        form.due_date = fields.Date.add(fields.Date.today(), days=10)
        self.assertIn(self.state, form.available_pricing_state_ids)
        self.assertIn(other_state, form.available_pricing_state_ids)
        form.pricing_state_id = self.state
        self.assertIn(self.area, form.available_pricing_county_area_ids)
        self.assertNotIn(inactive, form.available_pricing_county_area_ids)
        self.assertNotIn(other_area, form.available_pricing_county_area_ids)
        form.pricing_county_area_id = self.area
        self.assertIn(self.area, form.available_pricing_service_area_ids)
        self.assertIn(appraisal, form.available_pricing_service_area_ids)
        self.assertNotIn(other_area, form.available_pricing_service_area_ids)
        form.service_area_id = appraisal
        self.assertEqual(form.service_type, "appraisal")
        form.pricing_county_area_id = other_county
        self.assertFalse(form.service_area_id)
        self.assertFalse(form.service_type)
        self.assertEqual(form.county, other_county.county)
        form.service_area_id = other_county
        self.assertEqual(form.service_area_id, other_county)
        form.pricing_state_id = other_state
        self.assertFalse(form.pricing_county_area_id)
        self.assertFalse(form.service_area_id)
        self.assertFalse(form.service_type)
        form.pricing_county_area_id = other_area
        form.service_area_id = other_area
        self.assertEqual(form.service_type, "evaluation")
        form.pricing_state_id = self.state
        form.pricing_county_area_id = self.area
        form.service_area_id = self.area
        order = form.save()
        self.assertEqual(
            (order.service_area_id, order.service_type, order.state, order.county),
            (self.area, "evaluation", "E0", self.area.county),
        )
        self.assertEqual(
            self.state.with_context(trucalc_state_code_only=True).display_name,
            "E0",
        )
        self.assertEqual(
            self.area.with_context(
                trucalc_service_area_display="county"
            ).display_name,
            "Pricing County",
        )
        self.assertEqual(
            self.area.with_context(
                trucalc_service_area_display="service_type"
            ).display_name,
            "Evaluation",
        )
        self.assertNotEqual(
            self.area.display_name,
            self.area.with_context(
                trucalc_service_area_display="county"
            ).display_name,
        )

    def test_internal_submit_authority_and_mismatch_fail_closed(self):
        order = self.env["trucalc.order"].with_user(self.admin).with_context(
            trucalc_internal_draft_intake=True,
        ).create({
            "borrower": "Lock Authority",
            "property_address": "5 Lock Way",
            "company_id": self.bank_a.id,
            "pricing_state_id": self.state.id,
            "pricing_county_area_id": self.area.id,
            "service_area_id": self.area.id,
            "service_type": "appraisal",
            "state": "E0",
            "county": self.area.county,
            "due_date": fields.Date.add(fields.Date.today(), days=10),
        })
        with self.assertRaises(AccessError):
            order.with_user(self.bank_user).action_submit_internal_draft()
        with self.assertRaises(ValidationError):
            order.with_user(self.admin).action_submit_internal_draft()
        self.assertEqual(order.status, "draft")
        self.assertFalse(order.fee_locked_at)
        order.with_user(self.admin).service_type = "evaluation"
        self.area.with_user(self.admin).active = False
        with self.assertRaises(ValidationError):
            order.with_user(self.admin).action_submit_internal_draft()
        self.assertEqual(order.status, "draft")
        self.assertFalse(order.fee_locked_at)

    def test_internal_submit_requires_complete_exact_intake_atomically(self):
        complete = {
            "company_id": self.bank_a.id,
            "borrower": "Complete Intake",
            "property_address": "7 Complete Way",
            "due_date": fields.Date.add(fields.Date.today(), days=10),
            "pricing_state_id": self.state.id,
            "pricing_county_area_id": self.area.id,
            "service_area_id": self.area.id,
            "service_type": "evaluation",
            "state": "E0",
            "county": self.area.county,
        }
        for missing in (
            "company_id", "borrower", "property_address", "due_date",
            "pricing_state_id", "pricing_county_area_id", "service_area_id",
            "service_type",
        ):
            values = dict(complete)
            values.pop(missing)
            draft = self.env["trucalc.order"].with_user(
                self.admin
            ).with_context(trucalc_internal_draft_intake=True).create(values)
            with self.subTest(missing=missing), self.assertRaises(ValidationError):
                draft.with_user(self.admin).action_submit_internal_draft()
            self.assertEqual(draft.status, "draft")
            self.assertFalse(draft.order_date)
            self.assertFalse(draft.fee_locked_at)
            self.assertFalse(draft.lifecycle_event_ids.filtered(
                lambda event: event.event_type == "internal_request_submitted"
            ))

        other_state = self.env["res.country.state"].create({
            "name": "Forged State", "code": "FS",
            "country_id": self.env.ref("base.us").id,
        })
        forged_area = self.env["trucalc.service.area"].with_user(
            self.admin
        ).create({
            "state_id": other_state.id, "county": "Forged County",
            "service_type": "evaluation", "base_fee": 725,
        })
        forged_values = dict(complete, service_area_id=forged_area.id)
        forged = self.env["trucalc.order"].with_user(
            self.admin
        ).with_context(trucalc_internal_draft_intake=True).create(forged_values)
        with self.assertRaises(ValidationError):
            forged.with_user(self.admin).action_submit_internal_draft()
        self.assertEqual(forged.status, "draft")
        self.assertFalse(forged.fee_locked_at)

    def test_internal_submit_requires_configured_current_fee(self):
        unpriced = self.env["trucalc.service.area"].with_user(self.admin).create({
            "state_id": self.state.id,
            "county": "Unpriced County",
            "service_type": "evaluation",
        })
        draft = self.env["trucalc.order"].with_user(
            self.admin
        ).with_context(trucalc_internal_draft_intake=True).create({
            "company_id": self.bank_a.id,
            "borrower": "Unpriced Intake",
            "property_address": "8 Unpriced Way",
            "due_date": fields.Date.add(fields.Date.today(), days=10),
            "pricing_state_id": self.state.id,
            "pricing_county_area_id": unpriced.id,
            "service_area_id": unpriced.id,
            "service_type": "evaluation",
            "state": "E0",
            "county": unpriced.county,
        })
        with self.assertRaises(ValidationError):
            draft.with_user(self.admin).action_submit_internal_draft()
        self.assertEqual(draft.status, "draft")
        self.assertFalse(draft.order_date)
        self.assertFalse(draft.fee_locked_at)
        self.assertFalse(draft.lifecycle_event_ids.filtered(
            lambda event: event.event_type == "internal_request_submitted"
        ))
    def test_operations_can_submit_but_not_maintain_pricing(self):
        order = self.env["trucalc.order"].with_user(self.admin).with_context(
            trucalc_internal_draft_intake=True,
        ).create({
            "borrower": "Operations Pricing",
            "property_address": "6 Operations Way",
            "company_id": self.bank_a.id,
            "pricing_state_id": self.state.id,
            "pricing_county_area_id": self.area.id,
            "service_area_id": self.area.id,
            "service_type": "evaluation",
            "state": "E0",
            "county": self.area.county,
            "due_date": fields.Date.add(fields.Date.today(), days=10),
        })
        self.assertTrue(self.area.with_user(self.ops).has_access("read"))
        order.with_user(self.ops).action_submit_internal_draft()
        self.assertEqual(order.status, "new")
        self.assertEqual((order.agreed_fee, order.fee_source), (500, "base"))
        self.assertEqual(len(order.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "internal_request_submitted"
        )), 1)
        self.assertFalse(
            self.env["trucalc.negotiated.fee"].with_user(self.ops).has_access("read")
        )

    def test_removed_standalone_internal_actions_have_no_rpc_path(self):
        model = self.env["trucalc.order"]
        self.assertFalse(hasattr(model, "action_lock_pricing"))
        self.assertFalse(hasattr(model, "action_finalize_internal_draft"))

    def test_client_cannot_supply_snapshot_or_schedule_selection(self):
        values = self._bank_values()
        values["agreed_fee"] = 1
        with self.assertRaises(AccessError):
            self.env["trucalc.order"].with_user(
                self.bank_user
            )._create_bank_draft(values, self.bank_user)
