"""Read-only persistent candidate discovery for Location Correction A and B.

Run this file inside an Odoo shell and call ``find_candidates(env, login)``.
Unlike a raw SQL fee comparison, it applies record rules, the authoritative
server eligibility helper, and the same schedule resolver/currency comparison
used by the correction workflow.  It does not create or update records.
"""

from odoo.exceptions import AccessError, ValidationError


def find_candidates(env, actor_login):
    actor = env["res.users"].sudo().search([("login", "=", actor_login)], limit=1)
    if not actor:
        raise ValueError("No active validation actor has that login.")
    candidates = []
    orders = env["trucalc.order"].with_user(actor).search([])
    for order in orders:
        try:
            order._require_location_correction_actor()
            fee = order._validate_location_correction_eligibility()
        except (AccessError, ValidationError):
            continue
        alternates = env["trucalc.service.area"].sudo().search([
            ("active", "=", True),
            ("service_type", "=", order.service_type),
            ("id", "!=", order.service_area_id.id),
        ])
        for area in alternates:
            try:
                resolution = order._resolve_location_correction_pricing(
                    area.state_id, area.county,
                )
            except ValidationError:
                continue
            resolved = resolution["area"]
            county = resolution["county"]
            pricing = resolution["pricing"]
            if (
                pricing["fee_currency_id"] == fee["currency_id"]
                and resolution["direction"] == "same"
            ):
                candidates.append({
                    "order_id": order.id,
                    "order_number": order.order_number,
                    "status": order.status,
                    "current_service_area": order.service_area_id.display_name,
                    "corrected_state": resolved.state_id.code
                    or resolved.state_id.name,
                    "corrected_county": county,
                    "corrected_service_area": resolved.display_name,
                    "expected_fee": pricing["agreed_fee"],
                    "expected_fee_source": pricing["fee_source"],
                })
    return candidates


def find_higher_fee_candidates(env, actor_login):
    """Return accepted, pre-solicitation, higher-fee B candidates without writes."""
    actor = env["res.users"].sudo().search([("login", "=", actor_login)], limit=1)
    if not actor or not actor.active:
        raise ValueError("No active validation actor has that login.")
    candidates = []
    orders = env["trucalc.order"].with_user(actor).search([])
    for order in orders:
        try:
            order._require_location_correction_actor()
            fee = order._validate_location_correction_eligibility()
        except (AccessError, ValidationError):
            continue
        if order.status != "accepted" or not order.can_correct_property_location:
            continue
        alternates = env["trucalc.service.area"].sudo().search([
            ("active", "=", True),
            ("service_type", "=", order.service_type),
            ("id", "!=", order.service_area_id.id),
        ])
        for area in alternates:
            try:
                resolution = order._resolve_location_correction_pricing(
                    area.state_id, area.county,
                )
            except ValidationError:
                continue
            resolved = resolution["area"]
            county = resolution["county"]
            pricing = resolution["pricing"]
            if (
                pricing["fee_currency_id"] == fee["currency_id"]
                and resolution["direction"] == "higher"
            ):
                candidates.append({
                    "order_id": order.id,
                    "order_number": order.order_number,
                    "status": order.status,
                    "action_visible": order.can_correct_property_location,
                    "current_state": order.state,
                    "current_county": order.county,
                    "current_service_area": order.service_area_id.display_name,
                    "current_effective_fee": fee["amount"],
                    "corrected_state": resolved.state_id.code
                    or resolved.state_id.name,
                    "corrected_county": county,
                    "corrected_service_area": resolved.display_name,
                    "proposed_fee": pricing["agreed_fee"],
                    "proposed_fee_source": pricing["fee_source"],
                })
    return candidates
