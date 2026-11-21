"""Independent-process concurrency harness for Decline After Acceptance.

Run through ``odoo-bin shell`` only on a disposable database whose name starts
with ``trucalc_5b_decline_``. The harness commits its synthetic fixture.
"""
import json
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from odoo import Command, fields


assert env.cr.dbname.startswith("trucalc_5b_decline_")
suffix = fields.Datetime.now().strftime("%Y%m%d%H%M%S%f")


def user(name, groups, vendor=False):
    return env["res.users"].with_context(no_reset_password=True).create({
        "name": name,
        "login": "%s-%s" % (name, suffix),
        "email": "%s-%s@example.test" % (name, suffix),
        "group_ids": [Command.set([
            env.ref("trucalc_orders.%s" % group).id for group in groups
        ])],
        "trucalc_vendor_id": vendor.id if vendor else False,
    })


admin = user("decline-concurrency-admin", ["group_trucalc_admin"])
ops = user("decline-concurrency-ops", ["group_trucalc_operations"])
vendor = env["trucalc.vendor"].create({
    "name": "Decline Concurrency Vendor %s" % suffix,
})
vendor_user = user(
    "decline-concurrency-vendor", ["group_vendor_portal"], vendor=vendor,
)
env["trucalc.vendor.fee"].create({
    "vendor_id": vendor.id, "service_type": "evaluation", "fee": 40,
})
order = env["trucalc.order"].with_user(admin).create({
    "borrower": "Synthetic TC-00253 Concurrency",
    "property_address": "253 Concurrent Inspection Road",
    "service_type": "evaluation",
    "due_date": fields.Date.add(fields.Date.today(), days=4),
})
order.with_user(admin).action_accept_request()
order.with_user(admin).action_request_vendor_bids(
    vendor, fields.Datetime.now() + timedelta(days=2),
)
invitation = order.invitation_ids
bid = invitation.with_user(vendor_user).action_vendor_submit_response(
    "standard_terms_accepted",
)
bid.with_user(ops)._action_confirm_engagement()
authorization = order.vendor_authorization_ids.filtered(
    lambda item: item.active and item.source == "assignment"
)
env.flush_all()
env["trucalc.vendor.order"].invalidate_model()
env["trucalc.vendor.order"].with_user(vendor_user).browse(
    authorization.id
).action_vendor_accept_engagement()
env.cr.commit()


WORKER = r'''
import json
import time
from pathlib import Path
from odoo import api, SUPERUSER_ID
from odoo.exceptions import AccessError, ValidationError
from psycopg2.errors import SerializationFailure

root = Path(args['directory'])

def wait(name):
    deadline = time.monotonic() + 30
    while not (root / name).exists():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for ' + name)
        time.sleep(.02)

def operation(local):
    return local['trucalc.order'].browse(args['oid']).with_user(
        args['actor']
    ).action_decline_after_acceptance(
        'inspection_findings', args['reason'], args['notification'],
    )

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
if args['role'] == 'winner':
    env.cr.execute("SET LOCAL lock_timeout = '20s'")
    order._lock_for_bid_lifecycle()
    (root / 'locked').touch()
    wait('stale')
else:
    wait('locked')
    env.cr.execute("SET LOCAL lock_timeout = '20s'")
    order.read(['status', 'bidding_round', 'assigned_vendor_id'])
    (root / 'stale').touch()
try:
    operation(env)
    if args['role'] == 'winner':
        time.sleep(.3)
    env.cr.commit()
    result['outcome'] = 'ok'
except SerializationFailure:
    env.cr.rollback()
    result['serialization_retries'] = 1
    with env.registry.cursor() as cr:
        result['outcome'] = execute(api.Environment(cr, SUPERUSER_ID, {}))
except (AccessError, ValidationError):
    env.cr.rollback()
    result['outcome'] = 'rejected'
(root / (args['role'] + '.json')).write_text(json.dumps(result))
'''


directory = Path(tempfile.mkdtemp(prefix="trucalc_5b_decline_race_"))
processes = []
for role, actor, reason in (
    ("winner", admin.id, "Winner inspection findings"),
    ("contender", ops.id, "Stale competing inspection findings"),
):
    args = {
        "directory": str(directory),
        "oid": order.id,
        "actor": actor,
        "role": role,
        "reason": reason,
        "notification": "Vendor manually notified before concurrent action.",
    }
    command = [
        sys.executable, sys.argv[0], "shell",
        "-c", "/Users/Scott/Projects/odoo/debian/odoo.conf",
        "-d", env.cr.dbname, "--no-http", "--max-cron-threads=0",
        "--data-dir=/tmp/trucalc_5b_decline_concurrency_data",
        "--logfile=" + str(directory / (role + ".log")),
    ]
    output = (directory / (role + ".out")).open("w")
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=output,
        stderr=subprocess.STDOUT, text=True,
    )
    process.stdin.write("args = " + repr(args) + "\n" + WORKER)
    process.stdin.close()
    processes.append((process, output))

try:
    for process, output in processes:
        assert process.wait(timeout=90) == 0, str(directory)
        output.close()
finally:
    for process, output in processes:
        if process.poll() is None:
            process.kill()
            process.wait()
        output.close()

winner = json.loads((directory / "winner.json").read_text())
contender = json.loads((directory / "contender.json").read_text())
assert (winner["outcome"], contender["outcome"]) == ("ok", "rejected"), (
    winner, contender, str(directory),
)
env.cr.rollback()
env.invalidate_all()
order = env["trucalc.order"].browse(order.id)
engagement = env["trucalc.vendor.engagement"].with_context(
    active_test=False,
).search([("order_id", "=", order.id)])
authorizations = env[
    "trucalc.order.vendor.authorization"
].with_context(active_test=False).search([("order_id", "=", order.id)])
events = env["trucalc.order.lifecycle.event"].search([
    ("order_id", "=", order.id),
    ("event_type", "=", "order_declined_after_acceptance"),
])
close_events = env["trucalc.vendor.engagement.event"].search([
    ("engagement_id", "=", engagement.id),
    ("event_type", "=", "engagement_closed_order_declined"),
])
assert order.status == "declined"
assert order.decline_reason == "Winner inspection findings"
assert not engagement.active and engagement.closed_reason == "order_declined"
assert not authorizations.filtered("active")
assert len(events) == 1 and len(close_events) == 1
print(json.dumps({
    "result": "POST_ACCEPTANCE_DECLINE_CONCURRENCY_PASS",
    "winner": winner,
    "contender": contender,
    "order_id": order.id,
    "lifecycle_event_id": events.id,
    "directory": str(directory),
}), flush=True)
