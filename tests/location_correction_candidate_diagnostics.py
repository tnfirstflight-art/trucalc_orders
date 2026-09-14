"""Read-only persistent candidate discovery for Location Correction A.

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
                resolved, county = order._resolve_service_area(
                    area.state_id, area.county, order.service_type,
                )
                pricing = order._resolve_bank_fee(order.company_id, resolved)
            except ValidationError:
                continue
            if (
                pricing["fee_currency_id"] == fee["currency_id"]
                and order.fee_currency_id.compare_amounts(
                    pricing["agreed_fee"], fee["amount"]
                ) == 0
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
