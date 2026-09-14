"""Independent-process Location Correction A concurrency harness.

Run only through ``odoo-bin shell`` on a disposable database whose name starts
with ``trucalc_5a_location_``. The harness commits fixtures and race outcomes.
"""
import json
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from odoo import Command, SUPERUSER_ID, api, fields


assert env.cr.dbname.startswith("trucalc_5a_location_"), (
    "Disposable Location Correction database required"
)
registry = env.registry
company = env["res.company"].search([
    ("trucalc_is_bank", "=", True), ("trucalc_bank_active", "=", True),
], limit=1)
assert company, "An active disposable-test Bank fixture is required"
suffix = fields.Datetime.now().strftime("%Y%m%d%H%M%S%f")


def user(name, groups, bank=False, vendor=False):
    values = {
        "name": name,
        "login": "%s-%s" % (name, suffix),
        "company_id": bank.id if bank else env.company.id,
        "company_ids": [Command.set(bank.ids if bank else env.company.ids)],
        "group_ids": [Command.set([
            env.ref("trucalc_orders.%s" % group).id for group in groups
        ])],
        "trucalc_bank_company_id": bank.id if bank else False,
        "trucalc_vendor_id": vendor.id if vendor else False,
    }
    return env["res.users"].with_context(no_reset_password=True).create(values)


admin = user("location-concurrency-admin", ["group_trucalc_admin"])
admin.write({"company_ids": [Command.link(company.id)]})
bank_user = user(
    "location-concurrency-bank", ["group_bank_requestor"], bank=company,
)
vendor = env["trucalc.vendor"].create({"name": "Location Concurrency Vendor"})
env["trucalc.vendor.fee"].create({
    "vendor_id": vendor.id, "service_type": "evaluation", "fee": 300,
})
state = env["res.country.state"].search([
    ("code", "=", "MS"), ("country_id", "=", env.ref("base.us").id),
], limit=1)
Area = env["trucalc.service.area"].with_user(admin)
original = Area.create({
    "state_id": state.id, "county": "Concurrency Original %s" % suffix,
    "service_type": "evaluation", "base_fee": 500,
})
target_a = Area.create({
    "state_id": state.id, "county": "Concurrency Target A %s" % suffix,
    "service_type": "evaluation", "base_fee": 500,
})
target_b = Area.create({
    "state_id": state.id, "county": "Concurrency Target B %s" % suffix,
    "service_type": "evaluation", "base_fee": 500,
})
admin_id = admin.id
vendor_id = vendor.id


def setup():
    values = {
        "borrower": "Concurrency Borrower",
        "property_address": "1 Concurrency Way",
        "city": "Informational City", "zip_code": "38600",
        "loan_number": "LOCATION-CONCURRENCY", "service_type": "evaluation",
        "property_type": "commercial",
        "due_date": fields.Date.add(fields.Date.today(), days=14),
        "inspection_contact_name": "Concurrency Contact",
        "inspection_contact_phone": "9015550100",
        "inspection_contact_email": "concurrency@example.test", "notes": "",
        "service_area_id": str(original.id),
        "service_state_id": str(state.id), "service_county": original.county,
    }
    model = env["trucalc.order"].with_user(bank_user)
    order = model._create_bank_draft(values, bank_user)
    model._send_bank_draft(order, values, bank_user)
    order.with_user(admin).action_accept_request()
    env.cr.commit()
    return order.id


WORKER = r"""
import json
import time
from pathlib import Path
from odoo import api, SUPERUSER_ID, fields
from odoo.exceptions import AccessError, ValidationError
from psycopg2.errors import SerializationFailure

root = Path(args['directory'])

def wait(name):
    deadline = time.monotonic() + 20
    while not (root / name).exists():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for ' + name)
        time.sleep(.02)

def operation(local):
    order = local['trucalc.order'].browse(args['oid'])
    area = local['trucalc.service.area'].browse(args['target'])
    op = args['operation']
    if op == 'correct':
        return order.with_user(args['admin'])._apply_same_fee_location_correction(
            area.state_id, area.county, 'Concurrent correction', args['expected'],
        )
    if op == 'solicit':
        vendor = local['trucalc.vendor'].browse(args['vendor'])
        return order.with_user(args['admin']).action_request_vendor_bids(
            vendor, fields.Datetime.now() + __import__('datetime').timedelta(days=2),
        )
    if op == 'fee':
        return order.with_user(args['admin']).action_request_fee_change(
            600, 'Concurrent fee request',
        )
    if op == 'price':
        return area.with_user(args['admin']).write({'base_fee': 550})
    raise AssertionError(op)

def execute(local):
    try:
        operation(local)
        local.cr.commit()
        return 'ok'
    except (AccessError, ValidationError):
        local.cr.rollback()
        return 'rejected'

result = {'serialization_retries': 0}
order = env['trucalc.order'].browse(args['oid'])
area = env['trucalc.service.area'].browse(args['target'])
if args['role'] == 'winner':
    env.cr.execute("SET LOCAL lock_timeout = '10s'")
    if args['operation'] == 'price':
        env.cr.execute('SELECT id FROM trucalc_service_area WHERE id=%s FOR UPDATE', (area.id,))
    else:
        order._lock_for_bid_lifecycle()
        if args['operation'] == 'correct':
            order._lock_pricing_key(order.company_id.id, area.id)
            env.cr.execute(
                'SELECT id FROM trucalc_service_area WHERE id=%s FOR SHARE',
                (area.id,),
            )
    (root / 'locked').touch()
    wait('stale')
    try:
        operation(env)
        result['outcome'] = 'ok'
    except (AccessError, ValidationError):
        result['outcome'] = 'rejected'
    time.sleep(.3)
    env.cr.commit()
else:
    wait('locked')
    env.cr.execute("SET LOCAL lock_timeout = '10s'")
    order.read(['status', 'service_area_id', 'current_agreed_fee'])
    area.read(['base_fee'])
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
    except (AccessError, ValidationError):
        env.cr.rollback()
        result['outcome'] = 'rejected'
(root / (args['role'] + '.json')).write_text(json.dumps(result))
"""


def race(name, first, second, target, expected_first, expected_second):
    oid = setup()
    directory = Path(tempfile.mkdtemp(prefix="trucalc_5a_location_race_"))
    processes = []
    for role, operation in (("winner", first), ("contender", second)):
        args = {
            "directory": str(directory), "oid": oid,
            "admin": admin_id, "vendor": vendor_id,
            "target": target.id, "expected": original.id,
            "role": role, "operation": operation,
        }
        command = [
            sys.executable, sys.argv[0], "shell", "-c",
            "/Users/Scott/Projects/odoo/debian/odoo.conf",
            "-d", env.cr.dbname,
            "--http-port=%s" % (8081 if role == "winner" else 8082),
            "--max-cron-threads=0",
            "--data-dir=/tmp/trucalc_5a_location_concurrency_data",
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
        events = local["trucalc.order.lifecycle.event"].search([
            ("order_id", "=", oid),
            ("event_type", "=", "property_location_corrected"),
        ])
        pending = local["trucalc.fee.change.request"].search_count([
            ("order_id", "=", oid), ("state", "=", "pending"),
        ])
        assert len(events) <= 1
        assert pending <= 1
        assert order.agreed_fee == 500 and order.current_agreed_fee == 500
        assert order.original_service_area_id.id == original.id
        if events:
            assert events.correction_old_service_area_id.id == original.id
            assert events.correction_new_service_area_id == order.service_area_id
    print("PASS:", name, one, two, str(directory), flush=True)


cases = (
    ("correction vs correction", "correct", "correct", target_a, "ok", "rejected"),
    ("correction vs solicitation", "correct", "solicit", target_a, "ok", "ok"),
    ("solicitation vs correction", "solicit", "correct", target_a, "ok", "rejected"),
    ("correction vs fee request", "correct", "fee", target_a, "ok", "ok"),
    ("fee request vs correction", "fee", "correct", target_a, "ok", "rejected"),
    ("correction vs pricing update", "correct", "price", target_a, "ok", "ok"),
    ("pricing update vs correction", "price", "correct", target_b, "ok", "rejected"),
)
for case in cases:
    race(*case)
print(
    "PASS: 7 independent-process races; completion is structurally unreachable "
    "inside the approved pre-solicitation correction window",
    flush=True,
)
