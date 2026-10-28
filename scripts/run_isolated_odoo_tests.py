#!/usr/bin/env python3
"""Run Odoo tests only when database and filestore isolation agree."""

import argparse
import configparser
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile


MODULE_ROOT = Path(__file__).resolve().parents[1]
ODOO_ROOT = MODULE_ROOT.parents[1]
DEFAULT_CONFIG = ODOO_ROOT / "debian" / "odoo.conf"
DEFAULT_PYTHON = ODOO_ROOT / "venv" / "bin" / "python"
DEFAULT_ODOO_BIN = ODOO_ROOT / "odoo-bin"


def _is_within(path, parent):
    return path == parent or parent in path.parents


def _configured_databases(config_path):
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(config_path):
        raise ValueError("Cannot read Odoo config: %s" % config_path)
    raw = parser.get("options", "db_name", fallback="")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _guard(args, parser):
    if not re.fullmatch(r"[A-Za-z0-9_]+", args.database):
        parser.error("database must contain only letters, digits, and underscores")

    configured = _configured_databases(args.config)
    if args.database in configured:
        parser.error(
            "refusing isolated tests against configured application database %s"
            % args.database
        )
    if args.database == "trucalc_prod" or args.database.endswith("_prod"):
        parser.error("refusing isolated tests against a production database")

    expected_filter = "^%s$" % re.escape(args.database)
    if args.db_filter != expected_filter:
        parser.error(
            "dbfilter must exactly match the isolated database: %s"
            % expected_filter
        )

    data_dir = args.data_dir.resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    allowed_temp_roots = {Path("/tmp").resolve(), temp_root}
    normal_data_dir = (
        Path.home() / "Library" / "Application Support" / "Odoo"
    ).resolve()
    if _is_within(data_dir, normal_data_dir):
        parser.error("refusing to use the normal Odoo data directory")
    if not any(_is_within(data_dir, root) for root in allowed_temp_roots):
        parser.error(
            "isolated data directory must be under one of: %s"
            % ", ".join(sorted(map(str, allowed_temp_roots)))
        )

    protected = ("-d", "--database", "--db-filter", "--data-dir")
    for token in args.odoo_args:
        if token in protected or token.startswith("-d") or any(
            token.startswith(option + "=") for option in protected[1:]
        ):
            parser.error("do not override guarded Odoo option %s" % token)

    return data_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--db-filter", required=True)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--odoo-bin", type=Path, default=DEFAULT_ODOO_BIN)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("odoo_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.odoo_args[:1] == ["--"]:
        args.odoo_args = args.odoo_args[1:]

    try:
        data_dir = _guard(args, parser)
    except (configparser.Error, ValueError) as error:
        parser.error(str(error))

    command = [
        str(args.python), str(args.odoo_bin),
        "-c", str(args.config.resolve()),
        "-d", args.database,
        "--db-filter", args.db_filter,
        "--data-dir", str(data_dir),
        *args.odoo_args,
    ]
    print("ISOLATED_TEST_DATABASE=%s" % args.database)
    print("ISOLATED_TEST_DBFILTER=%s" % args.db_filter)
    print("ISOLATED_TEST_DATA_DIR=%s" % data_dir)
    print("COMMAND=%s" % shlex.join(command))
    if args.check_only:
        return 0
    os.execv(command[0], command)


if __name__ == "__main__":
    sys.exit(main())
