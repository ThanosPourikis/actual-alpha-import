"""Parse Alpha Bank Greece export files into structured transaction records.

Supports two formats:
- Bank account statement (tab or semicolon, Greek payees, has transaction ref number)
- Credit card statement (semicolon, Latin payees, card number as ref, has category)
"""

from __future__ import annotations

import decimal
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Column name used to detect the header row
COL_SEQ = "Α/Α"

# Each tuple lists accepted variants across export formats
COL_DATE_VARIANTS    = ("Ημ/νία", "Ημερομηνία")
COL_AMOUNT_VARIANTS  = ("Ποσό", "Ποσό (EUR)")
COL_SIGN_VARIANTS    = ("Πρόσημο ποσού", "Χρέωση/Πίστωση")
# Transaction ref: present in bank account exports; absent in credit card exports
COL_REF_VARIANTS     = ("Αρ. συναλλαγής",)
# Value date: present in bank account exports only
COL_INTEREST_DATE    = "Τοκισμός από"
COL_DESCRIPTION      = "Αιτιολογία"
COL_CATEGORY         = "Κατηγορία"
COL_BRANCH           = "Κατάστημα"

# Sign values (same across formats)
SIGN_DEBIT  = "Χ"   # expense, stored as negative
SIGN_CREDIT = "Π"   # income, stored as positive


@dataclass
class Transaction:
    date: date
    payee: str
    notes: str
    amount: decimal.Decimal
    import_id: str
    bank_category: str | None = None  # raw Κατηγορία value from credit card exports


# Greek Unicode characters that are visually identical to Latin letters.
# Bank account exports mix these in with real Latin chars.
_GREEK_TO_LATIN = str.maketrans({
    0x391: "A", 0x392: "B", 0x395: "E", 0x396: "Z",
    0x397: "H", 0x399: "I", 0x39A: "K", 0x39C: "M",
    0x39D: "N", 0x39F: "O", 0x3A1: "P", 0x3A4: "T",
    0x3A5: "Y", 0x3A7: "X",
})


def _normalize_payee(s: str) -> str:
    """Normalize a payee string to plain ASCII uppercase for matching."""
    return s.translate(_GREEK_TO_LATIN).upper().strip()


def _is_card_transaction(txn: "Transaction") -> bool:
    return txn.bank_category is not None


def dedup_bank_against_card(transactions: list["Transaction"]) -> list["Transaction"]:
    """Merge bank+card transaction pairs that represent the same real-world payment.

    When a bank entry and a card entry match (same amount, date within 3 days,
    payee substring match after normalization), the bank entry is merged into the
    card entry: the bank's unique transaction ref (import_id) and value date
    replace the card's composite import_id and date. The card's richer payee
    name and bank_category are kept.

    Unmatched bank transactions are kept as-is.
    """
    card = [t for t in transactions if _is_card_transaction(t)]
    bank = [t for t in transactions if not _is_card_transaction(t)]

    # Track which card transactions have already been merged to avoid double-merge
    merged_card_ids: set[str] = set()
    merged = 0
    kept_bank: list[Transaction] = []

    for bt in bank:
        bn = _normalize_payee(bt.payee)
        match = None
        if bn:
            for ct in card:
                if (
                    ct.import_id not in merged_card_ids
                    and abs(bt.amount) == abs(ct.amount)
                    and abs((bt.date - ct.date).days) <= 3
                    and bn in _normalize_payee(ct.payee)
                ):
                    match = ct
                    break

        if match:
            # Upgrade the card transaction in-place: use bank's stable import_id and date
            match.import_id = bt.import_id
            match.date = bt.date
            merged_card_ids.add(match.import_id)
            merged += 1
            logger.debug(
                "Merged bank ref %s into card payee %r (date: %s, amount: %s)",
                bt.import_id, match.payee, match.date, match.amount,
            )
        else:
            kept_bank.append(bt)

    if merged:
        logger.info(
            "Cross-file merge: upgraded %d card transaction(s) with bank ref numbers.",
            merged,
        )

    return card + kept_bank


class ParseError(Exception):
    pass


def _parse_greek_decimal(value: str) -> decimal.Decimal:
    """Convert a Greek-formatted number string to Decimal.

    Greek format: dot as thousands separator, comma as decimal separator.
    Example: '1.234,56' -> Decimal('1234.56')
    """
    value = value.strip()
    if not value:
        raise ParseError("Empty amount value")
    normalized = value.replace(".", "").replace(",", ".")
    try:
        return decimal.Decimal(normalized)
    except decimal.InvalidOperation as exc:
        raise ParseError(f"Cannot parse amount: {value!r}") from exc


def _parse_date(value: str) -> date:
    """Parse DD/MM/YYYY or D/M/YYYY date strings."""
    value = str(value).strip()
    for fmt in ("%d/%m/%Y", "%#d/%#m/%Y", "%-d/%-m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ParseError(f"Cannot parse date: {value!r}")


def _detect_delimiter(path: Path) -> str:
    """Detect tab vs semicolon delimiter from the first few lines."""
    with open(path, encoding="utf-8-sig") as f:
        sample = "".join(f.readline() for _ in range(8))
    return ";" if sample.count(";") > sample.count("\t") else "\t"


def _find_header_row(path: Path, delimiter: str) -> int:
    """Return the 0-based index of the row containing the column headers.

    Checks all cells (not just the first) to handle exports where an empty
    column precedes the sequence number column.
    """
    with open(path, encoding="utf-8-sig") as f:
        for i, line in enumerate(f):
            cells = [c.strip().strip('"') for c in line.split(delimiter)]
            if COL_SEQ in cells:
                return i
    raise ParseError(f"Could not find header row (no row contains {COL_SEQ!r})")


def _resolve(columns: list[str], variants: tuple[str, ...]) -> str | None:
    return next((v for v in variants if v in columns), None)


def parse_file(path: Path) -> list[Transaction]:
    """Parse an Alpha Bank Greece export file (bank account or credit card).

    Returns a list of Transaction objects. Skips empty or malformed rows
    and logs warnings for anything that cannot be parsed.
    """
    path = Path(path)
    delimiter = _detect_delimiter(path)

    try:
        header_row = _find_header_row(path, delimiter)
    except ParseError as exc:
        raise ParseError(f"{path.name}: {exc}") from exc

    df = pd.read_csv(
        path,
        sep=delimiter,
        encoding="utf-8-sig",
        skiprows=header_row,
        header=0,
        dtype=str,
        engine="python",
    )

    df.columns = df.columns.str.strip()
    df = df.apply(lambda col: col.str.strip() if col.dtype == object else col)

    # Strip Excel formula wrappers like ="value"
    def _strip_excel_wrapper(val):
        if isinstance(val, str) and val.startswith('="') and val.endswith('"'):
            return val[2:-1]
        return val

    for _col in df.select_dtypes(include="object").columns:
        df[_col] = df[_col].map(_strip_excel_wrapper)

    cols = list(df.columns)

    col_date   = _resolve(cols, COL_DATE_VARIANTS)
    col_amount = _resolve(cols, COL_AMOUNT_VARIANTS)
    col_sign   = _resolve(cols, COL_SIGN_VARIANTS)
    col_ref    = _resolve(cols, COL_REF_VARIANTS)   # None for credit card exports

    missing = [str(v) for v, name in [
        (col_date,   COL_DATE_VARIANTS),
        (col_amount, COL_AMOUNT_VARIANTS),
        (col_sign,   COL_SIGN_VARIANTS),
    ] if v is None]
    if missing or COL_DESCRIPTION not in cols:
        raise ParseError(f"{path.name}: missing required columns, candidates: {missing}")

    # Credit card exports have no unique transaction ref; flag so we use composite id
    is_card_export = col_ref is None
    if is_card_export:
        logger.debug("%s: no transaction ref column, will use composite import_id", path.name)

    transactions: list[Transaction] = []

    for lineno, row in df.iterrows():
        def _str(val) -> str:
            return "" if pd.isna(val) else str(val)

        raw_posting_date = _str(row.get(col_date, ""))
        raw_value_date   = _str(row.get(COL_INTEREST_DATE, "")) if COL_INTEREST_DATE in cols else ""
        raw_desc         = _str(row.get(COL_DESCRIPTION, ""))
        raw_amount       = _str(row.get(col_amount, ""))
        raw_sign         = _str(row.get(col_sign, ""))
        raw_ref          = _str(row.get(col_ref, "")) if col_ref else ""
        raw_branch       = _str(row.get(COL_BRANCH, "")) if COL_BRANCH in cols else ""
        raw_category     = _str(row.get(COL_CATEGORY, "")) if COL_CATEGORY in cols else ""

        if not raw_posting_date and not raw_amount and not raw_desc:
            continue

        # Use value date when available (when the money actually moved),
        # fall back to posting date.
        raw_date = raw_value_date if raw_value_date else raw_posting_date

        try:
            txn_date = _parse_date(raw_date)
        except ParseError:
            try:
                txn_date = _parse_date(raw_posting_date)
            except ParseError as exc:
                logger.warning("Row %s: skipping, %s", lineno, exc)
                continue

        try:
            amount = _parse_greek_decimal(raw_amount)
        except ParseError as exc:
            logger.warning("Row %s: skipping, %s", lineno, exc)
            continue

        if raw_sign == SIGN_DEBIT:
            amount = -amount
        elif raw_sign == SIGN_CREDIT:
            pass
        else:
            logger.warning("Row %s: unknown sign %r, treating as debit", lineno, raw_sign)
            amount = -amount

        # For bank account exports, use the unique transaction ref.
        # For credit card exports, build a composite from stable fields.
        if raw_ref and not is_card_export:
            import_id = raw_ref
        else:
            import_id = f"{raw_posting_date}|{raw_desc}|{raw_amount}|{raw_sign}"
            if not is_card_export:
                logger.debug("Row %s: no ref number, using composite import_id", lineno)

        # Credit card payees have the merchant location padded onto the end.
        # Collapse multiple spaces to detect and strip it.
        payee = " ".join(raw_desc.split())

        transactions.append(
            Transaction(
                date=txn_date,
                payee=payee,
                notes=raw_branch,
                amount=amount,
                import_id=import_id,
                bank_category=raw_category or None,
            )
        )

    return transactions
