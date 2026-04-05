"""Create Actual Budget categorisation rules from a YAML definition file."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml
from actual.queries import create_rule, get_or_create_category, get_rules
from actual.rules import Action, ActionType, Condition, ConditionType, Rule

from .parser import _normalize_payee

logger = logging.getLogger(__name__)

DEFAULT_RULES_FILE = Path(__file__).parent.parent.parent / "rules.yaml"


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_rule_definitions(path: Path) -> list[tuple[str, str, str]]:
    """Load rule definitions from a YAML file.

    Returns a flat list of (payee_fragment, group_name, category_name) tuples.
    Skips the reserved `alpha_bank_categories` key.
    """
    data = _load_yaml(path)

    rules: list[tuple[str, str, str]] = []
    for group_name, categories in data.items():
        if group_name == "alpha_bank_categories":
            continue
        if not isinstance(categories, dict):
            continue
        for category_name, fragments in categories.items():
            if not isinstance(fragments, list):
                continue
            for fragment in fragments:
                rules.append((str(fragment), group_name, category_name))
    return rules


def load_alpha_bank_category_map(path: Path) -> dict[str, tuple[str, str]]:
    """Return the alpha_bank_categories mapping as {bank_label: (group, category)}."""
    data = _load_yaml(path)
    result: dict[str, tuple[str, str]] = {}
    for bank_label, mapping in data.get("alpha_bank_categories", {}).items():
        result[bank_label] = (mapping["group"], mapping["category"])
    return result


def resolve_category(
    payee: str,
    bank_category: str | None,
    rule_definitions: list[tuple[str, str, str]],
    alpha_bank_map: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    """Return (group, category) for a transaction, or None if unresolved.

    YAML rules (payee fragment matching) take precedence over the Alpha Bank
    category mapping from the CSV.
    """
    normalized_payee = _normalize_payee(payee)
    for fragment, group, category in rule_definitions:
        if _normalize_payee(fragment) in normalized_payee:
            return group, category

    if bank_category and bank_category in alpha_bank_map:
        return alpha_bank_map[bank_category]

    return None


@dataclass
class RulesResult:
    created: int
    skipped: int


def _existing_rule_conditions(session) -> set[str]:
    """Return a set of 'field:op:value' strings for all existing rules.

    Used to avoid creating duplicate rules on repeated runs.
    """
    existing: set[str] = set()
    for db_rule in get_rules(session):
        raw = db_rule.conditions
        if not raw:
            continue
        try:
            conditions = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for cond in conditions:
            field = cond.get("field", "")
            op = cond.get("op", "")
            value = cond.get("value", "")
            existing.add(f"{field}:{op}:{value}")
    return existing


def setup_rules(
    session,
    rules_file: Path | None = None,
    dry_run: bool = False,
) -> RulesResult:
    """Create categorisation rules in Actual Budget from a YAML file.

    Skips rules whose condition already exists to make repeated runs safe.
    """
    path = rules_file or DEFAULT_RULES_FILE
    rule_definitions = load_rule_definitions(path)
    logger.debug("Loaded %d rule definitions from %s", len(rule_definitions), path)

    existing = _existing_rule_conditions(session)
    created = 0
    skipped = 0

    for payee_fragment, group_name, category_name in rule_definitions:
        condition_key = f"imported_description:contains:{payee_fragment}"

        if condition_key in existing:
            logger.debug("Skipping existing rule: %s", payee_fragment)
            skipped += 1
            continue

        if dry_run:
            logger.info(
                "Would create: imported_description contains %r -> %s / %s",
                payee_fragment, group_name, category_name,
            )
            created += 1
            continue

        category = get_or_create_category(session, category_name, group_name=group_name)

        condition = Condition(
            field="imported_description",
            op=ConditionType.CONTAINS,
            value=payee_fragment,
        )
        action = Action(
            field="category",
            op=ActionType.SET,
            value=category,
        )
        rule = Rule(
            conditions=[condition],
            actions=[action],
            operation="and",
        )

        create_rule(session, rule, run_immediately=False)
        logger.info(
            "Created: imported_description contains %r -> %s / %s",
            payee_fragment, group_name, category_name,
        )
        created += 1

    return RulesResult(created=created, skipped=skipped)
