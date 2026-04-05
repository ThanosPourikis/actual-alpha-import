"""CLI entrypoint for actual-alpha-import."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from .parser import ParseError, dedup_bank_against_card, parse_file

load_dotenv()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(format="%(levelname)s: %(message)s", level=level)


def _load_actual_config(account_override: str | None = None) -> dict:
    """Load and validate Actual connection settings from environment."""
    server_url = os.environ.get("ACTUAL_SERVER_URL", "http://localhost:5006")
    password = os.environ.get("ACTUAL_PASSWORD")
    file_name = os.environ.get("ACTUAL_FILE")
    account_name = account_override or os.environ.get("ACTUAL_ACCOUNT")
    encryption_password = os.environ.get("ACTUAL_ENCRYPTION_PASSWORD") or None

    missing = [name for name, val in [
        ("ACTUAL_PASSWORD", password),
        ("ACTUAL_FILE", file_name),
    ] if not val]
    if missing:
        click.echo(
            f"Error: missing required configuration: {', '.join(missing)}\n"
            "Set these as environment variables or in a .env file.",
            err=True,
        )
        sys.exit(1)

    return {
        "server_url": server_url,
        "password": password,
        "file_name": file_name,
        "account_name": account_name,
        "encryption_password": encryption_password,
    }


def _collect_files(paths: tuple[str, ...]) -> list[Path]:
    """Expand directories to CSV/TSV files and validate all paths exist."""
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            click.echo(f"Error: path does not exist: {p}", err=True)
            sys.exit(1)
        if p.is_dir():
            found = sorted(p.glob("*.csv")) + sorted(p.glob("*.tsv"))
            if not found:
                click.echo(f"Warning: no CSV/TSV files found in {p}", err=True)
            files.extend(found)
        else:
            files.append(p)
    return files


@click.group()
def cli() -> None:
    """Actual Budget tools for Alpha Bank Greece."""


@cli.command("import")
@click.argument("paths", nargs=-1, required=True, metavar="FILE_OR_DIR...")
@click.option("--dry-run", is_flag=True, help="Parse and print transactions without importing.")
@click.option("--account", default=None, help="Override the Actual account name.")
@click.option("--rules-file", default=None, type=click.Path(exists=True, path_type=Path), help="Path to a custom rules YAML file.")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def main(paths: tuple[str, ...], dry_run: bool, account: str | None, rules_file: Path | None, verbose: bool) -> None:
    """Import Alpha Bank Greece CSVs into Actual Budget.

    Accepts one or more CSV/TSV files, or a directory containing them.
    """
    _setup_logging(verbose)

    files = _collect_files(paths)
    if not files:
        click.echo("No files to process.", err=True)
        sys.exit(1)

    all_transactions = []
    for f in files:
        try:
            txns = parse_file(f)
            logging.info("Parsed %d transactions from %s", len(txns), f.name)
            all_transactions.extend(txns)
        except ParseError as exc:
            click.echo(f"Error parsing {f}: {exc}", err=True)
            sys.exit(1)

    if not all_transactions:
        click.echo("No transactions found.")
        sys.exit(0)

    all_transactions = dedup_bank_against_card(all_transactions)
    logging.info("After dedup: %d transaction(s)", len(all_transactions))

    if dry_run:
        click.echo(f"\nDry run: {len(all_transactions)} transaction(s) parsed\n")
        click.echo(f"{'Date':<12} {'Amount':>12}  {'Payee'}")
        click.echo("-" * 60)
        for t in all_transactions:
            click.echo(f"{t.date!s:<12} {t.amount:>12}  {t.payee}")
        click.echo(f"\nTotal: {len(all_transactions)} transaction(s). Nothing was imported.")
        return

    cfg = _load_actual_config(account)

    if not cfg["account_name"]:
        click.echo(
            "Error: missing required configuration: ACTUAL_ACCOUNT\n"
            "Set it as an environment variable, in a .env file, or use --account.",
            err=True,
        )
        sys.exit(1)

    from .importer import import_transactions

    try:
        result = import_transactions(
            transactions=all_transactions,
            server_url=cfg["server_url"],
            password=cfg["password"],
            file_name=cfg["file_name"],
            account_name=cfg["account_name"],
            encryption_password=cfg["encryption_password"],
            rules_file=rules_file,
        )
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    click.echo(
        f"\nDone: {result.total} parsed, "
        f"{result.added} added, "
        f"{result.modified} modified, "
        f"{result.skipped} skipped."
    )


@cli.command("setup-rules")
@click.option("--dry-run", is_flag=True, help="Print rules that would be created without saving.")
@click.option("--rules-file", default=None, type=click.Path(exists=True, path_type=Path), help="Path to a custom rules YAML file.")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def setup_rules_cmd(dry_run: bool, rules_file: Path | None, verbose: bool) -> None:
    """Create categorisation rules in Actual Budget for Alpha Bank payees."""
    _setup_logging(verbose)

    cfg = _load_actual_config()

    from actual import Actual
    from .rules import setup_rules

    with Actual(
        base_url=cfg["server_url"],
        password=cfg["password"],
        file=cfg["file_name"],
        encryption_password=cfg["encryption_password"],
    ) as actual:
        actual.download_budget()
        result = setup_rules(actual.session, rules_file=rules_file, dry_run=dry_run)
        if not dry_run:
            actual.commit()

    action = "Would create" if dry_run else "Created"
    click.echo(
        f"\n{action} {result.created} rule(s), skipped {result.skipped} already existing."
    )


if __name__ == "__main__":
    cli()
