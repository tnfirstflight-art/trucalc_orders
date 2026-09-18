"""Independent-process Location Correction B concurrency harness.

Run through ``odoo-bin shell`` only on a disposable database whose name starts
with ``trucalc_5a_location_b_``. The harness commits fixtures and race results.
"""
import json
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from odoo import Command, SUPERUSER_ID, api, fields


assert env.cr.dbname.startswith("trucalc_5a_location_b_")
registry = env.registry
suffix = fields.Datetime.now().strftime("%Y%m%d%H%M%S%f")


def user(name, groups, bank=False):
    values = {
        "name": name,
        "login": "%s-%s" % (name, suffix),
        "company_id": bank.id if bank else env.company.id,
        "company_ids": [Command.set(bank.ids if bank else env.company.ids)],
        "group_ids": [Command.set([
            env.ref("trucalc_orders.%s" % group).id for group in groups
        ])],
        "trucalc_bank_company_id": bank.id if bank else False,
    }
    return env["res.users"].with_context(no_reset_password=True).create(values)


admin = user("location-b-concurrency-admin", ["group_trucalc_admin"])
company = env["res.company"].with_user(admin)._trucalc_create_bank({
    "name": "Location B Concurrency Bank %s" % suffix,
    "currency_id": env.company.currency_id.id,
})
bank_user = user(
    "location-b-concurrency-requestor", ["group_bank_requestor"], bank=company,
)
bank_admin = user(
    "location-b-concurrency-bank-admin", ["group_bank_admin"], bank=company,
)
vendor = env["trucalc.vendor"].create({"name": "Location B Race Vendor"})
env["trucalc.vendor.fee"].create({
    "vendor_id": vendor.id, "service_type": "evaluation", "fee": 300,
})
state = env["res.country.state"].search([
    ("code", "=", "MS"), ("country_id", "=", env.ref("base.us").id),
], limit=1)
Area = env["trucalc.service.area"].with_user(admin)
original = Area.create({
    "state_id": state.id, "county": "Location B Original %s" % suffix,
    "service_type": "evaluation", "base_fee": 500,
})
target = Area.create({
    "state_id": state.id, "county": "Location B Higher %s" % suffix,
    "service_type": "evaluation", "base_fee": 600,
})
alternate = Area.create({
    "state_id": state.id, "county": "Location B Alternate %s" % suffix,
    "service_type": "evaluation", "base_fee": 650,
})
admin_id = admin.id
bank_admin_id = bank_admin.id
vendor_id = vendor.id


def setup(staged=False):
    values = {
        "borrower": "Location B Race Borrower",
        "property_address": "1 Race Way",
        "city": "Informational City", "zip_code": "38600",
        "loan_number": "LOCATION-B-RACE", "service_type": "evaluation",
        "property_type": "commercial",
        "due_date": fields.Date.add(fields.Date.today(), days=14),
        "inspection_contact_name": "Race Contact",
        "inspection_contact_phone": "9015550100",
        "inspection_contact_email": "race@example.test", "notes": "",
        "service_area_id": str(original.id),
        "service_state_id": str(state.id), "service_county": original.county,
    }
    model = env["trucalc.order"].with_user(bank_user)
    order = model._create_bank_draft(values, bank_user)
    model._send_bank_draft(order, values, bank_user)
    order.with_user(admin).action_accept_request()
    if staged:
        order.with_user(admin)._stage_higher_fee_location_correction(
            target.state_id, target.county, "Race correction", original.id,
        )
    env.cr.commit()
    return order.id


WORKER = r"""
import json
import time
from pathlib import Path
from odoo import api, SUPERUSER_ID, fields
from odoo.exceptions import AccessError, ValidationError
from psycopg2.errors import SerializationFailure, UniqueViolation

root = Path(args['directory'])

def wait(name):
    deadline = time.monotonic() + 20
    while not (root / name).exists():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for ' + name)
        time.sleep(.02)

def operation(local):
    order = local['trucalc.order'].browse(args['oid'])
    target = local['trucalc.service.area'].browse(args['target'])
    op = args['operation']
    if op in ('stage', 'stage2'):
        return order.with_user(args['admin'])._stage_higher_fee_location_correction(
            target.state_id, target.county, 'Concurrent correction',
            order.service_area_id.id,
        )
    if op == 'solicit':
        vendor = local['trucalc.vendor'].browse(args['vendor'])
        return order.with_user(args['admin']).action_request_vendor_bids(
            vendor, fields.Datetime.now() + __import__('datetime').timedelta(days=2),
        )
    request = local['trucalc.fee.change.request'].search([
        ('order_id', '=', order.id), ('state', '=', 'pending'),
        ('location_correction_request_id', '!=', False),
    ], limit=1)
    if op == 'approve':
        return request.with_user(args['bank_admin'])._decide(order, 'approved')
    if op == 'decline':
        return request.with_user(args['bank_admin'])._decide(
            order, 'declined', 'Concurrent decline',
        )
    if op == 'price':
        return target.with_user(args['admin']).write({'base_fee': args['new_fee']})
    if op == 'cancel':
        order._lock_for_bid_lifecycle()
        return order._controlled_lifecycle_write({'status': 'cancelled'})
    raise AssertionError(op)

def execute(local):
    try:
        operation(local)
        local.cr.commit()
        return 'ok'
    except (AccessError, ValidationError, UniqueViolation):
        local.cr.rollback()
        return 'rejected'

result = {'serialization_retries': 0}
order = env['trucalc.order'].browse(args['oid'])
area = env['trucalc.service.area'].browse(args['target'])
if args['role'] == 'winner':
    env.cr.execute("SET LOCAL lock_timeout = '10s'")
    if args['operation'] == 'price':
        order._lock_pricing_key(order.company_id.id, area.id)
        env.cr.execute(
            'SELECT id FROM trucalc_service_area WHERE id=%s FOR UPDATE',
            (area.id,),
        )
    else:
        order._lock_for_bid_lifecycle()
        if args['operation'] in ('stage', 'stage2'):
            order._lock_pricing_key(order.company_id.id, area.id)
            env.cr.execute(
                'SELECT id FROM trucalc_service_area WHERE id=%s FOR SHARE',
                (area.id,),
            )
        if args['operation'] in ('approve', 'decline'):
            request = env['trucalc.fee.change.request'].search([
                ('order_id', '=', order.id), ('state', '=', 'pending'),
            ], limit=1)
            env.cr.execute(
                'SELECT id FROM trucalc_fee_change_request WHERE id=%s FOR UPDATE',
                (request.id,),
            )
            request.location_correction_request_id._lock()
    (root / 'locked').touch()
    wait('stale')
    try:
        operation(env)
        result['outcome'] = 'ok'
    except (AccessError, ValidationError, UniqueViolation):
        env.cr.rollback()
        result['outcome'] = 'rejected'
    time.sleep(.3)
    env.cr.commit()
else:
    wait('locked')
    env.cr.execute("SET LOCAL lock_timeout = '10s'")
    order.read(['status', 'service_area_id', 'current_agreed_fee'])
    area.read(['base_fee', 'active'])
    env['trucalc.fee.change.request'].search([
        ('order_id', '=', order.id), ('state', '=', 'pending'),
    ]).read(['state', 'proposed_fee'])
    (root / 'stale').touch()
    try:
        operation(env)
        env.cr.commit()
        result['outcome'] = 'ok'
    except SerializationFailure:
        env.cr.rollback()
        result['serialization_retries'] = 1
        with env.registry.cursor() as cr:
            local = api.Environment(cr, SUPERUSER_ID, {})
            result['outcome'] = execute(local)
    except (AccessError, ValidationError, UniqueViolation):
        env.cr.rollback()
        result['outcome'] = 'rejected'
(root / (args['role'] + '.json')).write_text(json.dumps(result))
"""


def race(name, first, second, first_target, second_target,
         staged, expected_first, expected_second, new_fee=625):
    oid = setup(staged=staged)
    directory = Path(tempfile.mkdtemp(prefix="trucalc_5a_location_b_race_"))
    processes = []
    for role, operation, area in (
        ("winner", first, first_target), ("contender", second, second_target),
    ):
        args = {
            "directory": str(directory), "oid": oid,
            "admin": admin_id, "bank_admin": bank_admin_id,
            "vendor": vendor_id, "target": area.id,
            "role": role, "operation": operation, "new_fee": new_fee,
        }
        command = [
            sys.executable, sys.argv[0], "shell", "-c",
            "/Users/Scott/Projects/odoo/debian/odoo.conf",
            "-d", env.cr.dbname,
            "--http-port=%s" % (8082 if role == "winner" else 8083),
            "--max-cron-threads=0",
            "--data-dir=/tmp/trucalc_5a_location_b_concurrency_data",
            "--logfile=" + str(directory / (role + ".log")),
        ]
        output = (directory / (role + ".out")).open("w")
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=output,
            stderr=subprocess.STDOUT, text=True,
        )
        process.stdin.write("args = %r\n%s" % (args, WORKER))
        process.stdin.close()
        processes.append((process, output))
    try:
        for process, output in processes:
            assert process.wait(timeout=45) == 0, str(directory)
            output.close()
    finally:
        for process, output in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
            output.close()
    one = json.loads((directory / "winner.json").read_text())
    two = json.loads((directory / "contender.json").read_text())
    assert one["outcome"] == expected_first, (name, one, str(directory))
    assert two["outcome"] == expected_second, (name, two, str(directory))
    with registry.cursor() as cr:
        local = api.Environment(cr, SUPERUSER_ID, {})
        order = local["trucalc.order"].browse(oid)
        corrections = local["trucalc.location.correction.request"].search([
            ("order_id", "=", oid),
        ])
        assert len(corrections.filtered(lambda item: item.state == "pending")) <= 1
        assert len(corrections.filtered(lambda item: item.state == "applied")) <= 1
        assert order.agreed_fee == 500
        assert order.original_service_area_id.id == original.id
        if order.current_agreed_fee != 500:
            applied = corrections.filtered(lambda item: item.state == "applied")
            assert len(applied) == 1
            assert order.service_area_id == applied.proposed_service_area_id
            assert order.current_agreed_fee == applied.proposed_schedule_fee
        if order.status == "bid_requested":
            assert not corrections.filtered(
                lambda item: item.state in ("pending", "declined")
            )
    print("PASS:", name, one, two, str(directory), flush=True)


race("two correction proposals", "stage", "stage2", target, alternate,
     False, "ok", "rejected")
race("higher-fee correction vs Request Bids", "stage", "solicit", target,
     target, False, "ok", "rejected")
race("approval vs Request Bids", "approve", "solicit", target, target,
     True, "ok", "ok")
race("pricing update vs approval", "price", "approve", target, target,
     True, "ok", "rejected")
race("decline vs new correction", "decline", "stage2", target, alternate,
     True, "ok", "ok")
race("cancellation vs approval", "cancel", "approve", target, target,
     True, "ok", "rejected")
print("PASS: 6 independent-process Location Correction B races", flush=True)
