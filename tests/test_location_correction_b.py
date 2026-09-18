from datetime import timedelta
from unittest.mock import patch

from lxml import html

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .test_location_correction import TestLocationCorrectionA
from .test_bank_order_portal import TestBankOrderPortal
from .location_correction_candidate_diagnostics import find_higher_fee_candidates


@tagged("post_install", "-at_install", "trucalc_location_correction_b")
class TestLocationCorrectionB(TestLocationCorrectionA):
    def _stage(self, order, area=None, actor=None, reason="Correct county and fee"):
        area = area or self.higher_area
        return order.with_user(
            actor or self.admin
        )._stage_higher_fee_location_correction(
            area.state_id, area.county, reason, order.service_area_id.id,
        )

    def _wizard(self, order, area):
        return self.env["trucalc.location.correction.wizard"].with_user(
            self.admin
        ).create({
            "order_id": order.id,
            "current_state_id": order.service_area_id.state_id.id,
            "current_county": order.county,
            "current_service_area_id": order.service_area_id.id,
            "service_type": order.service_type,
            "currency_id": order.fee_currency_id.id,
            "current_effective_fee": order.current_agreed_fee,
            "corrected_state_id": area.state_id.id,
            "corrected_county_area_id": area.id,
            "correction_reason": "Correct persistent location",
        })

    def test_pricing_direction_and_resolver_parity(self):
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        expected = {
            self.higher_area: ("higher", "base", 550, 50),
            self.lower_area: ("lower", "base", 450, -50),
            self.same_area: ("same", "base", 500, 0),
            # Monetary storage rounds 500.004 to USD precision before resolve.
            self.same_state_area: ("same", "base", 500, 0),
        }
        for area, result in expected.items():
            resolution = order._resolve_location_correction_pricing(
                area.state_id, area.county,
            )
            self.assertEqual((
                resolution["direction"], resolution["pricing"]["fee_source"],
                resolution["pricing"]["agreed_fee"], resolution["difference"],
            ), result)

        higher_schedule = self.env["trucalc.negotiated.fee"].with_user(
            self.admin
        ).create({
            "bank_id": self.bank.id,
            "service_area_id": self.higher_area.id,
            "negotiated_fee": 525,
        })
        lower_schedule = self.env["trucalc.negotiated.fee"].with_user(
            self.admin
        ).create({
            "bank_id": self.bank.id,
            "service_area_id": self.lower_area.id,
            "negotiated_fee": 475,
        })
        for area, schedule, direction in (
            (self.higher_area, higher_schedule, "higher"),
            (self.lower_area, lower_schedule, "lower"),
        ):
            resolution = order._resolve_location_correction_pricing(
                area.state_id, area.county,
            )
            self.assertEqual((
                resolution["direction"], resolution["pricing"]["fee_source"],
                resolution["pricing"]["negotiated_fee_id"],
            ), (direction, "negotiated", schedule.id))

    def test_candidate_preview_and_confirmation_recompute_are_identical(self):
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        candidates = find_higher_fee_candidates(self.env, self.admin.login)
        candidate = next(
            item for item in candidates
            if item["order_id"] == order.id
            and item["corrected_county"] == self.higher_area.county
        )
        self.assertEqual((
            candidate["proposed_fee"], candidate["proposed_fee_source"],
        ), (550, "base"))

        preview = self.env["trucalc.location.correction.wizard"].new({
            "order_id": order.id,
            "corrected_state_id": self.higher_area.state_id.id,
            "corrected_county_area_id": self.higher_area.id,
        })
        preview._preview_correction()
        self.assertEqual((
            preview.corrected_service_area_id, preview.corrected_schedule_fee,
            preview.fee_difference, preview.fee_result,
        ), (self.higher_area, 550, 50, "higher"))

        confirmation = self._wizard(order, self.higher_area)
        self.assertFalse(confirmation.fee_result)
        confirmation.action_confirm()
        correction = order.location_correction_request_ids
        self.assertEqual((
            correction.proposed_service_area_id,
            correction.proposed_schedule_fee,
            correction.pricing_source,
        ), (self.higher_area, 550, "base"))

    def test_lower_wizard_confirmation_recomputes_and_leaves_no_partial_state(self):
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        confirmation = self._wizard(order, self.lower_area)
        self.assertFalse(confirmation.fee_result)
        with self.assertRaisesRegex(ValidationError, "Lower-fee"):
            confirmation.action_confirm()
        self.assertFalse(order.location_correction_request_ids)
        self.assertFalse(order.fee_change_request_ids)
        self.assertEqual((order.service_area_id, order.current_agreed_fee), (
            self.original_area, 500,
        ))

    def test_higher_fee_stages_normal_fee_request_without_order_mutation(self):
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        before = (
            order.state, order.county, order.service_area_id,
            order.pricing_state_id, order.pricing_county_area_id,
            order.agreed_fee, order.current_agreed_fee,
            order.original_service_area_id,
        )
        correction = self._stage(order)
        fee_request = correction.fee_change_request_id
        self.assertEqual(before, (
            order.state, order.county, order.service_area_id,
            order.pricing_state_id, order.pricing_county_area_id,
            order.agreed_fee, order.current_agreed_fee,
            order.original_service_area_id,
        ))
        self.assertEqual(
            (correction.state, correction.old_service_area_id,
             correction.proposed_service_area_id,
             correction.prior_effective_fee,
             correction.proposed_schedule_fee,
             fee_request.state, fee_request.prior_fee,
             fee_request.proposed_fee,
             fee_request.location_correction_request_id),
            ("pending", self.original_area, self.higher_area, 500, 550,
             "pending", 500, 550, correction),
        )
        event = self.env["trucalc.order.lifecycle.event"].search([
            ("location_correction_request_id", "=", correction.id),
            ("event_type", "=", "property_location_correction_requested"),
        ])
        self.assertEqual(len(event), 1)
        self.assertEqual(event.fee_change_request_id, fee_request)
        for operation in (
            lambda: correction.write({"proposed_county": "Forged"}),
            lambda: correction.unlink(),
            lambda: correction.copy(),
        ):
            with self.assertRaises(AccessError):
                operation()
        with self.assertRaises(ValidationError):
            self._stage(order, self.same_state_area)

    def test_new_requires_accept_before_higher_fee_staging(self):
        order = self._priced()
        with self.assertRaisesRegex(ValidationError, "Accept this Order"):
            self._stage(order)
        self.assertFalse(order.location_correction_request_ids)
        self.assertFalse(order.fee_change_request_ids)
        order.with_user(self.admin).action_accept_request()
        self.assertTrue(self._stage(order))

    def test_bank_approval_applies_location_and_fee_atomically(self):
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        original = (
            order.agreed_fee, order.original_service_area_id, order.fee_source,
            order.negotiated_fee_id, order.fee_locked_at,
        )
        correction = self._stage(order)
        fee_request = correction.fee_change_request_id
        for actor in (
            self.bank_view, self.vendor_user, self.ops,
        ):
            with self.assertRaises(AccessError):
                fee_request.with_user(actor)._decide(order, "approved")
        fee_request.with_user(self.bank_admin)._decide(order, "approved")
        self.assertEqual(original, (
            order.agreed_fee, order.original_service_area_id, order.fee_source,
            order.negotiated_fee_id, order.fee_locked_at,
        ))
        self.assertEqual(
            (order.state, order.county, order.service_area_id,
             order.pricing_state_id, order.pricing_county_area_id,
             order.current_agreed_fee, order.current_fee_change_request_id,
             correction.state),
            ("Q2", "Higher County", self.higher_area,
             self.state_b, self.higher_area, 550, fee_request, "applied"),
        )
        event_types = set(self.env["trucalc.order.lifecycle.event"].search([
            ("fee_change_request_id", "=", fee_request.id),
        ]).mapped("event_type"))
        self.assertEqual(event_types, {
            "fee_change_requested", "fee_change_approved",
            "property_location_correction_requested",
            "property_location_corrected",
        })
        self.assertEqual(order.internal_fee_change_outcome, "approved")
        self.assertFalse(order.location_correction_blocked)

    def test_approved_badge_clears_at_bid_requested_and_later(self):
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        correction = self._stage(order)
        correction.fee_change_request_id.with_user(self.bank_admin)._decide(
            order, "approved",
        )
        self.assertEqual(order.internal_fee_change_outcome, "approved")
        for status in (
            "bid_requested", "assigned", "engaged", "report_received",
            "reviewer_assigned", "under_review", "completed",
        ):
            order._controlled_lifecycle_write({"status": status})
            self.assertFalse(
                order.internal_fee_change_outcome,
                "Approved badge remained visible at %s" % status,
            )

    def test_bank_requestor_approval_and_decline_use_shared_atomic_path(self):
        approved = self._priced()
        approved.with_user(self.admin).action_accept_request()
        approved_correction = self._stage(approved)
        approved_request = approved_correction.fee_change_request_id
        approved_request.with_user(self.bank_user)._decide(
            approved, "approved",
        )
        self.assertEqual((
            approved.state, approved.county, approved.service_area_id,
            approved.current_agreed_fee, approved_correction.state,
            approved_request.state, approved_request.decision_actor_id,
        ), (
            "Q2", "Higher County", self.higher_area, 550, "applied",
            "approved", self.bank_user,
        ))

        declined = self._priced()
        declined.with_user(self.admin).action_accept_request()
        before = (
            declined.state, declined.county, declined.service_area_id,
            declined.current_agreed_fee,
        )
        declined_correction = self._stage(declined)
        declined_request = declined_correction.fee_change_request_id
        declined_request.with_user(self.bank_user)._decide(
            declined, "declined", "Requestor rejected corrected-location fee",
        )
        self.assertEqual((
            declined.state, declined.county, declined.service_area_id,
            declined.current_agreed_fee,
        ), before)
        self.assertEqual((
            declined_correction.state, declined_request.state,
            declined_request.decision_actor_id,
            declined.location_correction_blocked,
        ), ("declined", "declined", self.bank_user, True))

    def test_cross_bank_requestor_cannot_decide_linked_fee(self):
        other_bank = self.env["res.company"].with_context(
            trucalc_test_bank_fixture=True,
        ).create({
            "name": "5A Location Foreign Decision Bank",
            "trucalc_is_bank": True,
            "trucalc_bank_active": True,
        })
        foreign_requestor = self._user(
            "5a-location-foreign-requestor",
            ["group_bank_requestor"], bank=other_bank,
        )
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        correction = self._stage(order)
        with self.assertRaises(AccessError):
            correction.fee_change_request_id.with_user(
                foreign_requestor
            )._decide(order, "approved")
        self.assertEqual((
            correction.state, correction.fee_change_request_id.state,
            order.service_area_id, order.current_agreed_fee,
        ), ("pending", "pending", self.original_area, 500))

    def test_decline_applies_nothing_and_blocks_solicitation(self):
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        before = (
            order.state, order.county, order.service_area_id,
            order.agreed_fee, order.current_agreed_fee,
        )
        correction = self._stage(order)
        correction.fee_change_request_id.with_user(self.bank_admin)._decide(
            order, "declined", "Bank rejected corrected-location fee",
        )
        self.assertEqual(before, (
            order.state, order.county, order.service_area_id,
            order.agreed_fee, order.current_agreed_fee,
        ))
        self.assertEqual(correction.state, "declined")
        self.assertTrue(order.location_correction_blocked)
        self.assertEqual(order.internal_fee_change_outcome, "declined")
        self.assertTrue(order.with_user(self.admin).can_correct_property_location)
        self.assertFalse(order.can_request_vendor_bids)
        with self.assertRaisesRegex(ValidationError, "unresolved"):
            order.with_user(self.admin).action_request_vendor_bids(
                self.vendor, fields.Datetime.now() + timedelta(days=2),
            )
        decline_event = self.env["trucalc.order.lifecycle.event"].search([
            ("location_correction_request_id", "=", correction.id),
            ("event_type", "=", "property_location_correction_fee_declined"),
        ])
        self.assertEqual(len(decline_event), 1)

        order.with_user(self.admin).action_cancelled()
        self.assertEqual(order.status, "cancelled")
        self.assertFalse(order.location_correction_blocked)
        self.assertFalse(order.internal_fee_change_outcome)

    def test_new_higher_proposal_after_decline_preserves_history(self):
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        first = self._stage(order)
        first.fee_change_request_id.with_user(self.bank_admin)._decide(
            order, "declined", "Not approved",
        )
        second = self._stage(order, self.current_fee_area)
        self.assertEqual((first.state, second.state), ("declined", "pending"))
        self.assertEqual(order.internal_fee_change_outcome, "declined")
        self.assertNotEqual(first, second)
        self.assertEqual(
            self.env["trucalc.location.correction.request"].search_count([
                ("order_id", "=", order.id), ("state", "=", "pending"),
            ]),
            1,
        )
        with self.assertRaises(AccessError):
            first.write({"state": "pending"})
        second.fee_change_request_id.with_user(self.bank_admin)._decide(
            order, "approved",
        )
        self.assertFalse(order.location_correction_blocked)
        self.assertEqual(order.internal_fee_change_outcome, "approved")

    def test_same_fee_correction_after_decline_clears_block(self):
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        correction = self._stage(order)
        correction.fee_change_request_id.with_user(self.bank_admin)._decide(
            order, "declined", "Try another location",
        )
        self._correct(order, self.same_area)
        latest = order.location_correction_request_ids.sorted(
            key=lambda item: item.id, reverse=True,
        )[:1]
        self.assertEqual(latest.state, "applied")
        self.assertFalse(latest.fee_change_request_id)
        self.assertFalse(order.location_correction_blocked)
        self.assertFalse(order.internal_fee_change_outcome)
        self.assertTrue(order.can_request_vendor_bids)
        self.assertEqual(order.service_area_id, self.same_area)

    def test_lower_fee_remains_blocked_without_staging(self):
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        with self.assertRaisesRegex(ValidationError, "Lower-fee"):
            self._stage(order, self.lower_area)
        self.assertFalse(order.location_correction_request_ids)
        self.assertFalse(order.fee_change_request_ids)
        self.assertEqual(order.service_area_id, self.original_area)

    def test_unrelated_pending_fee_change_blocks_location_staging(self):
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        order.with_user(self.admin).action_request_fee_change(525, "Other scope")
        with self.assertRaisesRegex(ValidationError, "pending Fee Change"):
            self._stage(order)
        self.assertFalse(order.location_correction_request_ids)

    def test_base_pricing_and_service_area_drift_fail_closed(self):
        for drift in ("price", "inactive"):
            with self.env.cr.savepoint():
                order = self._priced()
                order.with_user(self.admin).action_accept_request()
                correction = self._stage(order)
                if drift == "price":
                    self.higher_area.with_user(self.admin).write({"base_fee": 575})
                else:
                    self.higher_area.with_user(self.admin).write({"active": False})
                with self.assertRaisesRegex(
                    ValidationError, "pricing changed|no longer available|No active",
                ):
                    correction.fee_change_request_id.with_user(
                        self.bank_admin
                    )._decide(order, "approved")
                self.assertEqual(
                    (correction.state, order.service_area_id,
                     order.current_agreed_fee),
                    ("pending", self.original_area, 500),
                )

    def test_negotiated_pricing_and_current_fee_drift_fail_closed(self):
        schedule = self.env["trucalc.negotiated.fee"].with_user(
            self.admin
        ).create({
            "bank_id": self.bank.id,
            "service_area_id": self.higher_area.id,
            "negotiated_fee": 540,
        })
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        correction = self._stage(order)
        schedule.with_user(self.admin).write({"negotiated_fee": 545})
        with self.assertRaisesRegex(ValidationError, "pricing changed"):
            correction.fee_change_request_id.with_user(
                self.bank_admin
            )._decide(order, "approved")
        self.assertEqual((order.service_area_id, order.current_agreed_fee), (
            self.original_area, 500,
        ))

        with self.env.cr.savepoint():
            other = self._priced()
            other.with_user(self.admin).action_accept_request()
            other_correction = self._stage(other, self.current_fee_area)
            other._controlled_lifecycle_write({"current_agreed_fee": 501})
            with self.assertRaises(ValidationError):
                other_correction.fee_change_request_id.with_user(
                    self.bank_admin
                )._decide(other, "approved")
            self.assertEqual(other.service_area_id, self.original_area)

    def test_staging_and_approval_roll_back_as_atomic_units(self):
        order = self._priced()
        order.with_user(self.admin).action_accept_request()
        Event = type(self.env["trucalc.order.lifecycle.event"])
        with patch.object(
            Event, "_log_location_correction_request",
            side_effect=ValidationError("Injected request audit failure"),
        ):
            with self.assertRaises(ValidationError):
                self._stage(order)
        self.assertFalse(order.location_correction_request_ids)
        self.assertFalse(order.fee_change_request_ids)
        self.assertEqual(order.fee_workflow_revision, 0)

        correction = self._stage(order)
        fee_request = correction.fee_change_request_id
        with patch.object(
            Event, "_log_location_correction_decision",
            side_effect=ValidationError("Injected decision audit failure"),
        ):
            with self.assertRaises(ValidationError):
                fee_request.with_user(self.bank_admin)._decide(order, "approved")
        self.assertEqual(
            (correction.state, fee_request.state, order.service_area_id,
             order.current_agreed_fee, order.fee_workflow_revision),
            ("pending", "pending", self.original_area, 500, 1),
        )

    def test_internal_warning_and_history_contract(self):
        arch = self.env.ref("trucalc_orders.view_trucalc_order_form").arch_db
        for phrase in (
            "Pending Location Correction", "Location Correction Declined",
            "Location Correction History", "Submit another correction or cancel",
        ):
            self.assertIn(phrase, arch)


@tagged("post_install", "-at_install", "trucalc_location_correction_b_portal")
class TestLocationCorrectionBPortal(TestBankOrderPortal):
    def test_linked_pending_fee_uses_shared_decision_authority(self):
        model = self.env["trucalc.order"].with_user(self.bank_requestor)
        values = self._complete_draft_values()
        order = model._create_bank_draft(values, self.bank_requestor)
        model._send_bank_draft(order, values, self.bank_requestor)
        order.with_user(self.admin).action_accept_request()
        original = order.service_area_id
        target = self.env["trucalc.service.area"].with_user(self.admin).create({
            "state_id": original.state_id.id,
            "county": "Higher Fee Portal County",
            "service_type": order.service_type,
            "base_fee": order.current_agreed_fee + 100,
        })
        correction = order.with_user(
            self.admin
        )._stage_higher_fee_location_correction(
            target.state_id, target.county, "Correct portal county",
            original.id,
        )
        listing = "/my/trucalc/bank/orders"
        detail = "/my/trucalc/bank/orders/%s/documents" % order.order_number
        for actor in (self.bank_requestor, self.bank_viewer, self.bank_admin):
            self._login(actor)
            page = self.url_open(listing)
            self.assertEqual(page.status_code, 200)
            tree = html.fromstring(page.content)
            row = tree.xpath("//tr[@data-order-id='%s']" % order.id)
            self.assertEqual(len(row), 1)
            self.assertIn("o_trucalc_fee_pending", row[0].get("class", ""))
            can_decide = actor in (self.bank_requestor, self.bank_admin)
            buttons = row[0].xpath(
                ".//button[normalize-space()='Fee Change']"
            )
            self.assertEqual(len(buttons), int(can_decide))
            modal = tree.xpath("//div[@id='fee-change-%s']" % order.id)
            self.assertEqual(len(modal), int(can_decide))
            if modal:
                self.assertEqual(buttons[0].get("data-bs-toggle"), "modal")
                self.assertEqual(
                    buttons[0].get("data-bs-target"),
                    "#fee-change-%s" % order.id,
                )
                text = modal[0].text_content()
                for phrase in (
                    "Pending Property Location Correction", "Current Location",
                    "Proposed Location", "Proposed Service Area",
                    "Current Effective Fee", "Proposed Fee",
                    "Correct portal county", "Pending Approval",
                ):
                    self.assertIn(phrase, text)
                actions = modal[0].xpath(".//form/@action")
                decision_url = "/my/trucalc/bank/orders/%s/fee/%s" % (
                    order.order_number, correction.fee_change_request_id.id,
                )
                self.assertIn(decision_url + "/approved", actions)
                self.assertIn(decision_url + "/declined", actions)
            detail_page = self.url_open(detail)
            self.assertEqual(detail_page.status_code, 200)
            self.assertIn("Location Correction", detail_page.text)
            self.assertIn("Higher Fee Portal County", detail_page.text)
            if not can_decide:
                self.assertFalse(html.fromstring(detail_page.content).xpath(
                    "//form[contains(@action, '/fee/')]"
                ))
        correction.invalidate_recordset()
        self.assertEqual(correction.state, "pending")
        self.assertEqual(order.service_area_id, original)
