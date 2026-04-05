"""Push parsed transactions into Actual Budget via actualpy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from actual import Actual
from actual.queries import get_account, get_or_create_category, reconcile_transaction

from .parser import Transaction
from .rules import (
    DEFAULT_RULES_FILE,
    load_alpha_bank_category_map,
    load_rule_definitions,
    resolve_category,
)

logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    total: int
    added: int
    modified: int
    skipped: int


def import_transactions(
    transactions: list[Transaction],
    server_url: str,
    password: str,
    file_name: str,
    account_name: str,
    encryption_password: str | None = None,
    rules_file: Path | None = None,
) -> ImportResult:
    """Push a list of parsed transactions into Actual Budget.

    Category resolution order:
    1. YAML payee rules (fragment match against payee name)
    2. Alpha Bank Κατηγορία mapping from the CSV (credit card exports only)
    3. No category set (left blank for manual categorisation)

    Uses reconcile_transaction so the import is idempotent.
    """
    path = rules_file or DEFAULT_RULES_FILE
    rule_definitions = load_rule_definitions(path) if path.exists() else []
    alpha_bank_map = load_alpha_bank_category_map(path) if path.exists() else {}

    total = len(transactions)
    added = 0
    modified = 0
    skipped = 0

    with Actual(
        base_url=server_url,
        password=password,
        file=file_name,
        encryption_password=encryption_password,
    ) as actual:
        actual.download_budget()

        account = get_account(actual.session, account_name)
        if account is None:
            raise ValueError(
                f"Account {account_name!r} not found in Actual. "
                "Create it first or check the ACTUAL_ACCOUNT setting."
            )

        already_matched: list = []

        for txn in transactions:
            try:
                resolved = resolve_category(
                    txn.payee, txn.bank_category, rule_definitions, alpha_bank_map
                )

                category = None
                if resolved:
                    group_name, category_name = resolved
                    category = get_or_create_category(
                        actual.session, category_name, group_name=group_name
                    )
                    logger.debug(
                        "Category: %s / %s for %r", group_name, category_name, txn.payee
                    )

                result = reconcile_transaction(
                    actual.session,
                    date=txn.date,
                    account=account,
                    payee=txn.payee,
                    notes=txn.notes if txn.notes else None,
                    amount=txn.amount,
                    imported_id=txn.import_id,
                    imported_payee=txn.payee,
                    category=category,
                    already_matched=already_matched,
                )
                already_matched.append(result)

                if result.changed():
                    modified += 1
                    logger.debug("Modified: %s %s %s", txn.date, txn.payee, txn.amount)
                else:
                    added += 1
                    logger.debug("Added: %s %s %s", txn.date, txn.payee, txn.amount)

            except Exception as exc:
                logger.warning("Skipped transaction %r: %s", txn.import_id, exc)
                skipped += 1

        actual.commit()

    return ImportResult(total=total, added=added, modified=modified, skipped=skipped)
