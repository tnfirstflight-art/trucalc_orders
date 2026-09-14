from unittest.mock import patch

from lxml import etree

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_location_correction")
class TestLocationCorrectionA(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls._user("5a-location-admin", ["group_trucalc_admin"])
        cls.ops = cls._user("5a-location-ops", ["group_trucalc_operations"])
        cls.admin_reviewer = cls._user(
            "5a-location-admin-reviewer",
            ["group_trucalc_admin", "group_trucalc_reviewer"],
        )
        cls.ops_reviewer = cls._user(
            "5a-location-ops-reviewer",
            ["group_trucalc_operations", "group_trucalc_reviewer"],
        )
        Companies = cls.env["res.company"].with_context(
            trucalc_test_bank_fixture=True
        )
        cls.bank = Companies.create({
            "name": "5A Location Bank",
            "trucalc_is_bank": True,
            "trucalc_bank_active": True,
        })
        for actor in (cls.admin, cls.ops, cls.admin_reviewer, cls.ops_reviewer):
            actor.write({"company_ids": [Command.link(cls.bank.id)]})
        cls.bank_user = cls._user(
            "5a-location-bank", ["group_bank_requestor"], bank=cls.bank,
        )
        cls.bank_admin = cls._user(
            "5a-location-bank-admin", ["group_bank_admin"], bank=cls.bank,
        )
        cls.bank_view = cls._user(
            "5a-location-bank-view", ["group_bank_view_only"], bank=cls.bank,
        )
        cls.ordinary = cls._user("5a-location-ordinary", [])
        cls.plain_portal = cls._user(
            "5a-location-portal", ["base.group_portal"],
        )
        cls.vendor = cls.env["trucalc.vendor"].create({
            "name": "5A Location Vendor",
        })
        cls.vendor_user = cls._user(
            "5a-location-vendor", ["group_vendor_portal"],
            vendor=cls.vendor,
        )
        country = cls.env.ref("base.us")
        cls.state_a = cls.env["res.country.state"].create({
            "name": "5A Location State A", "code": "Q1",
            "country_id": country.id,
        })
        cls.state_b = cls.env["res.country.state"].create({
            "name": "5A Location State B", "code": "Q2",
            "country_id": country.id,
        })
        Area = cls.env["trucalc.service.area"].with_user(cls.admin)
        cls.original_area = Area.create({
            "state_id": cls.state_a.id, "county": "Original County",
            "service_type": "evaluation", "base_fee": 500,
        })
        cls.same_area = Area.create({
            "state_id": cls.state_a.id, "county": "Corrected County",
            "service_type": "evaluation", "base_fee": 500,
        })
        cls.same_state_area = Area.create({
            "state_id": cls.state_b.id, "county": "State Change County",
            "service_type": "evaluation", "base_fee": 500.004,
        })
        cls.higher_area = Area.create({
            "state_id": cls.state_b.id, "county": "Higher County",
            "service_type": "evaluation", "base_fee": 550,
        })
        cls.lower_area = Area.create({
            "state_id": cls.state_b.id, "county": "Lower County",
            "service_type": "evaluation", "base_fee": 450,
        })
        cls.current_fee_area = Area.create({
            "state_id": cls.state_b.id, "county": "Current Fee County",
            "service_type": "evaluation", "base_fee": 600,
        })

    @classmethod
    def _user(cls, login, groups, bank=False, vendor=False):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login,
            "login": login,
            "email": "%s@example.test" % login,
            "group_ids": [Command.set([
                cls.env.ref(
                    group if "." in group else "trucalc_orders.%s" % group
                ).id
                for group in groups
            ])],
            "trucalc_bank_company_id": bank.id if bank else False,
            "trucalc_vendor_id": vendor.id if vendor else False,
            "company_id": bank.id if bank else cls.env.company.id,
            "company_ids": [Command.set(bank.ids if bank else cls.env.company.ids)],
        })

    def _bank_values(self):
        return {
            "borrower": "Location Borrower",
            "property_address": "1 Original Way",
            "city": "Informational City",
            "zip_code": "38103",
            "loan_number": "LOCATION-A",
            "service_type": "evaluation",
            "property_type": "commercial",
            "due_date": fields.Date.add(fields.Date.today(), days=10),
            "inspection_contact_name": "Location Contact",
            "inspection_contact_phone": "9015550100",
            "inspection_contact_email": "location@example.test",
            "notes": "",
            "service_area_id": str(self.original_area.id),
            "service_state_id": str(self.state_a.id),
            "service_county": self.original_area.county,
        }

    def _priced(self):
        model = self.env["trucalc.order"].with_user(self.bank_user)
        order = model._create_bank_draft(self._bank_values(), self.bank_user)
        model._send_bank_draft(order, self._bank_values(), self.bank_user)
        return order.sudo()

    def _correct(self, order, area, actor=None, reason="  County corrected  "):
        return order.with_user(actor or self.admin)._apply_same_fee_location_correction(
            area.state_id, area.county, reason, order.service_area_id.id,
        )

    def test_base_same_fee_correction_and_exact_immutable_event(self):
        order = self._priced()
        before = (
            order.agreed_fee, order.current_agreed_fee, order.fee_source,
            order.negotiated_fee_id, order.fee_locked_at, order.city,
            order.zip_code, order.vendor_fee, order.due_date,
            order.original_service_area_id,
        )
        self._correct(order, self.same_area)
        self.assertEqual(
            (order.state, order.county, order.service_area_id,
             order.pricing_state_id, order.pricing_county_area_id),
            ("Q1", "Corrected County", self.same_area,
             self.state_a, self.same_area),
        )
        self.assertEqual(before, (
            order.agreed_fee, order.current_agreed_fee, order.fee_source,
            order.negotiated_fee_id, order.fee_locked_at, order.city,
            order.zip_code, order.vendor_fee, order.due_date,
            order.original_service_area_id,
        ))
        event = order.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "property_location_corrected"
        )
        self.assertEqual(len(event), 1)
        self.assertEqual(
            (event.correction_old_state, event.correction_old_county,
             event.correction_old_service_area_id,
             event.correction_new_state, event.correction_new_county,
             event.correction_new_service_area_id,
             event.correction_original_service_area_id,
             event.correction_current_fee, event.correction_schedule_fee,
             event.correction_fee_source, event.correction_reason,
             event.actor_id, event.from_status, event.to_status),
            ("Q1", "Original County", self.original_area,
             "Q1", "Corrected County", self.same_area,
             self.original_area, 500, 500, "base", "County corrected",
             self.admin, "new", "new"),
        )
        with self.assertRaises(AccessError):
            event.write({"correction_reason": "changed"})
        with self.assertRaises(AccessError):
            event.unlink()

    def test_state_change_currency_rounding_and_accepted_order(self):
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        self._correct(order, self.same_state_area, self.ops)
        self.assertEqual((order.state, order.county), ("Q2", "State Change County"))
        self.assertEqual(order.current_agreed_fee, 500)

    def test_negotiated_and_source_change_equal_fee(self):
        schedule = self.env["trucalc.negotiated.fee"].with_user(self.admin).create({
            "bank_id": self.bank.id,
            "service_area_id": self.same_area.id,
            "negotiated_fee": 500,
        })
        order = self._priced()
        self._correct(order, self.same_area)
        event = order.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "property_location_corrected"
        )
        self.assertEqual(
            (event.correction_fee_source,
             event.correction_negotiated_fee_id,
             order.fee_source, order.negotiated_fee_id),
            ("negotiated", schedule, "base",
             self.env["trucalc.negotiated.fee"]),
        )

    def test_comparison_uses_current_fee_after_prior_bank_approval(self):
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        request_id = order.with_user(self.admin).action_request_fee_change(
            600, "Prior approved scope change",
        )
        request = self.env["trucalc.fee.change.request"].browse(request_id)
        request.with_user(self.bank_admin)._decide(order, "approved")
        self._correct(order, self.current_fee_area, self.ops)
        self.assertEqual(
            (order.agreed_fee, order.current_agreed_fee,
             order.current_fee_change_request_id,
             order.original_service_area_id, order.service_area_id),
            (500, 600, request, self.original_area, self.current_fee_area),
        )
        event = order.lifecycle_event_ids.filtered(
            lambda item: item.event_type == "property_location_corrected"
        )
        self.assertEqual(
            (event.correction_current_fee, event.correction_schedule_fee),
            (600, 600),
        )

    def test_higher_and_lower_fee_are_atomic_blocks(self):
        for area, phrase in (
            (self.higher_area, "fee-approval workflow"),
            (self.lower_area, "Lower-fee"),
        ):
            order = self._priced()
            before = (
                order.state, order.county, order.service_area_id,
                order.pricing_state_id, order.pricing_county_area_id,
                order.agreed_fee, order.current_agreed_fee,
                order.original_service_area_id,
            )
            with self.assertRaisesRegex(ValidationError, phrase):
                self._correct(order, area)
            self.assertEqual(before, (
                order.state, order.county, order.service_area_id,
                order.pricing_state_id, order.pricing_county_area_id,
                order.agreed_fee, order.current_agreed_fee,
                order.original_service_area_id,
            ))
            self.assertFalse(order.lifecycle_event_ids.filtered(
                lambda item: item.event_type == "property_location_corrected"
            ))
            self.assertFalse(order.fee_change_request_ids)

    def test_authority_including_additive_reviewer(self):
        for actor in (
            self.admin, self.ops, self.admin_reviewer, self.ops_reviewer,
        ):
            order = self._priced()
            self._correct(order, self.same_area, actor)
        order = self._priced()
        for actor in (
            self.bank_admin, self.bank_user, self.bank_view,
            self.vendor_user, self.plain_portal, self.ordinary,
        ):
            with self.assertRaises(AccessError):
                self._correct(order, self.same_area, actor)
            self.assertFalse(
                self.env["trucalc.location.correction.wizard"].with_user(
                    actor
                ).has_access("create")
            )
        original = type(self.admin_reviewer).has_group

        def reviewer_only(user, group):
            if group in (
                "trucalc_orders.group_trucalc_admin",
                "trucalc_orders.group_trucalc_operations",
            ):
                return False
            return original(user, group)

        with patch.object(type(self.admin_reviewer), "has_group", reviewer_only):
            with self.assertRaises(AccessError):
                self._correct(order, self.same_area, self.admin_reviewer)

    def test_pending_fee_and_all_status_boundaries(self):
        pending = self._priced()
        pending.with_user(self.admin).action_accept_request()
        pending.with_user(self.admin).action_request_fee_change(600, "More scope")
        with self.assertRaisesRegex(ValidationError, "pending Fee Change"):
            self._correct(pending, self.same_area)
        for status in (
            "bid_requested", "assigned", "engaged", "report_received",
            "reviewer_assigned", "under_review", "completed", "cancelled",
            "declined", "draft",
        ):
            order = self._priced()
            order._controlled_lifecycle_write({"status": status})
            with self.assertRaises(ValidationError):
                self._correct(order, self.same_area)

    def test_stale_downstream_evidence_and_stale_wizard_are_blocked(self):
        order = self._priced()
        order._controlled_lifecycle_write({"bidding_round": 1})
        with self.assertRaisesRegex(ValidationError, "downstream"):
            self._correct(order, self.same_area)
        first = self._priced()
        expected = first.service_area_id.id
        self._correct(first, self.same_area)
        with self.assertRaisesRegex(ValidationError, "changed after"):
            first.with_user(self.admin)._apply_same_fee_location_correction(
                self.same_state_area.state_id, self.same_state_area.county,
                "Stale wizard", expected,
            )

    def test_legacy_missing_selectors_remain_eligible_but_conflicts_hide_action(self):
        order = self._priced()
        self.env.cr.execute(
            """
                UPDATE trucalc_order
                   SET pricing_state_id = NULL, pricing_county_area_id = NULL
                 WHERE id = %s
            """,
            (order.id,),
        )
        self.env.invalidate_all()
        self.assertTrue(order.with_user(self.admin).can_correct_property_location)
        order.with_user(self.admin)._validate_location_correction_eligibility()

        self.env.cr.execute(
            "UPDATE trucalc_order SET pricing_state_id = %s WHERE id = %s",
            (self.state_b.id, order.id),
        )
        self.env.invalidate_all()
        self.assertFalse(order.with_user(self.admin).can_correct_property_location)
        with self.assertRaisesRegex(ValidationError, "selector provenance"):
            order._validate_location_correction_eligibility()

    def test_ui_flag_matches_server_for_eligibility_matrix(self):
        eligible = self._priced()
        accepted = self._priced()
        accepted.with_user(self.admin).action_accept_request()
        bank_model = self.env["trucalc.order"].with_user(self.bank_user)
        unlocked = bank_model._create_bank_draft(
            self._bank_values(), self.bank_user,
        ).sudo()
        unlocked._controlled_lifecycle_write({"status": "new"})
        bid_requested = self._priced()
        bid_requested._controlled_lifecycle_write({"status": "bid_requested"})
        malformed_downstream = self._priced()
        self.env.cr.execute(
            "UPDATE trucalc_order SET vendor_fee = 1 WHERE id = %s",
            (malformed_downstream.id,),
        )
        self.env.invalidate_all()
        pending = self._priced()
        pending.with_user(self.admin).action_accept_request()
        pending.with_user(self.admin).action_request_fee_change(600, "More scope")

        for actor in (self.admin, self.ops):
            for label, order, expected in (
                ("eligible", eligible, True),
                ("accepted", accepted, True),
                ("unlocked", unlocked, False),
                ("bid_requested", bid_requested, False),
                ("malformed_downstream", malformed_downstream, False),
                ("pending", pending, False),
            ):
                with self.subTest(actor=actor.login, case=label, order=order.id):
                    visible = order.with_user(
                        actor
                    ).can_correct_property_location
                    try:
                        order.with_user(
                            actor
                        )._validate_location_correction_eligibility()
                        server_eligible = True
                    except ValidationError:
                        server_eligible = False
                    self.assertEqual(visible, server_eligible)
                    self.assertEqual(visible, expected)

        status_values = dict(
            eligible._fields["status"]._description_selection(self.env)
        )
        self.assertEqual(status_values["accepted"], "Accepted")

    def test_button_is_group_filtered_in_rendered_form_view(self):
        view_id = self.env.ref("trucalc_orders.view_trucalc_order_form").id
        button_xpath = "//button[@name='action_open_location_correction_wizard']"
        for actor in (self.admin, self.ops, self.admin_reviewer, self.ops_reviewer):
            arch = etree.fromstring(
                self.env["trucalc.order"].with_user(actor).get_view(
                    view_id=view_id, view_type="form",
                )["arch"].encode()
            )
            self.assertEqual(len(arch.xpath(button_xpath)), 1)
        for actor in (
            self.bank_admin, self.bank_user, self.bank_view, self.vendor_user,
            self.plain_portal, self.ordinary,
        ):
            try:
                view = self.env["trucalc.order"].with_user(actor).get_view(
                    view_id=view_id, view_type="form",
                )
            except AccessError:
                continue
            arch = etree.fromstring(view["arch"].encode())
            self.assertFalse(arch.xpath(button_xpath))

    def test_direct_writes_and_original_provenance_rewrite_are_blocked(self):
        order = self._priced()
        self.assertEqual(order.original_service_area_id, self.original_area)
        for values in (
            {"state": "Q2"}, {"county": "Forged"},
            {"service_area_id": self.same_area.id},
            {"pricing_state_id": self.state_b.id},
            {"pricing_county_area_id": self.same_area.id},
            {"service_type": "appraisal"},
            {"original_service_area_id": self.same_area.id},
        ):
            with self.assertRaises(AccessError):
                order.with_user(self.admin).write(values)
        with self.assertRaises(AccessError):
            order._controlled_lifecycle_write({
                "original_service_area_id": self.same_area.id,
            })

    def test_wizard_preview_form_and_internal_button_contract(self):
        order = self._priced()
        action = order.with_user(self.ops).action_open_location_correction_wizard()
        self.assertEqual(
            (action["res_model"], action["target"]),
            ("trucalc.location.correction.wizard", "new"),
        )
        form = Form(
            self.env["trucalc.location.correction.wizard"].with_user(
                self.ops
            ).with_context(default_order_id=order.id),
            view="trucalc_orders.view_trucalc_location_correction_wizard_form",
        )
        self.assertEqual(form.current_service_area_id, self.original_area)
        form.corrected_county_area_id = self.same_area
        self.assertEqual(form.corrected_service_area_id, self.same_area)
        self.assertEqual(form.fee_result, "same")
        form.correction_reason = "Verified county"
        view = etree.fromstring(self.env.ref(
            "trucalc_orders.view_trucalc_order_form"
        ).arch.encode())
        button = view.xpath(
            "//button[@name='action_open_location_correction_wizard']"
        )[0]
        self.assertEqual(button.get("invisible"), "not can_correct_property_location")
        self.assertEqual(
            button.get("groups"),
            "trucalc_orders.group_trucalc_admin,"
            "trucalc_orders.group_trucalc_operations",
        )
