# actual-alpha-import

Import Alpha Bank Greece transaction CSVs into [Actual Budget](https://actualbudget.org/) using the [actualpy](https://github.com/bvanelli/actualpy) library.

## Features

- Parses both Alpha Bank export formats:
  - **Bank account** statements (tab or semicolon separated, Greek payee names, unique transaction ref number)
  - **Credit card** statements (semicolon separated, Latin merchant names with city, bank-assigned category)
- Cross-file deduplication: when importing bank + card exports together, card entries are upgraded with the bank's unique transaction ref number so the import is idempotent
- YAML-based categorisation rules with payee fragment matching (Greek Unicode lookalike normalisation built in)
- Falls back to Alpha Bank's own `Κατηγορία` field for uncategorised card transactions
- `setup-rules` command to push YAML rules into Actual Budget as native rules
- Dry-run mode for previewing parsed transactions without touching Actual
- Accepts one file, multiple files, or a directory of CSVs

## Installation

```bash
uv sync
```

Or with pip:

```bash
pip install -e .
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `ACTUAL_SERVER_URL` | URL to your Actual Budget server (default: `http://localhost:5006`) |
| `ACTUAL_PASSWORD` | Actual server password |
| `ACTUAL_FILE` | Budget file name |
| `ACTUAL_ACCOUNT` | Account name in Actual to import into |
| `ACTUAL_ENCRYPTION_PASSWORD` | Optional: budget encryption password |

## Usage

### Import transactions

```bash
# Single file
uv run actual-alpha-import import transactions.csv

# Multiple files (bank + card together — deduplicates automatically)
uv run actual-alpha-import import bank.csv card.csv

# All CSVs in a directory
uv run actual-alpha-import import ./exports/

# Dry run — preview without importing
uv run actual-alpha-import import --dry-run transactions.csv

# Override the target account
uv run actual-alpha-import import --account "Alpha Savings" transactions.csv

# Use a custom rules file
uv run actual-alpha-import import --rules-file my-rules.yaml transactions.csv
```

### Set up categorisation rules

Push all YAML rules into Actual Budget as native rules:

```bash
uv run actual-alpha-import setup-rules

# Preview without saving
uv run actual-alpha-import setup-rules --dry-run

# Use a custom rules file
uv run actual-alpha-import setup-rules --rules-file my-rules.yaml
```

## Categorisation rules

Copy `rules.yaml.example` to `rules.yaml` and customise it:

```bash
cp rules.yaml.example rules.yaml
```

The structure is `Group > Category > [payee fragments]`. Any transaction whose payee contains a fragment (case-insensitive, Greek lookalike chars normalised) is assigned that category.

```yaml
Food:
  Groceries:
    - "ΑΒ_SΗΟΡ"       # AB Vassilopoulos
    - "SΚLΑVΕΝΙΤΙS"
  Restaurants:
    - "ΚFC"

Transfers:
  Revolut:
    - "RΕVΟLUΤ"
```

Rules are checked in file order — put more specific fragments before broader ones (e.g. a specific tax payment fragment before a generic `ΙRΙS` catch-all).

The optional `alpha_bank_categories` section maps Alpha Bank's own credit card categories to Actual categories as a fallback when no payee rule matches. See `rules.yaml.example` for the full list.

`rules.yaml` is gitignored as it contains personal payee names. Only `rules.yaml.example` is committed.

## Alpha Bank export formats

### Bank account statement

- Tab or semicolon separated, UTF-8 BOM
- Columns include: `Αρ. συναλλαγής` (unique transaction ref), `Τοκισμός από` (value date), `Αιτιολογία` (description)
- Sign: `Χ` = debit, `Π` = credit

### Credit card statement (ENTER BONUS)

- Semicolon separated, UTF-8 BOM
- Merchant names in Latin characters with city appended
- Includes `Κατηγορία` (Alpha Bank category) column
- No unique transaction ref — composite ID used instead
- When imported alongside the bank statement, card entries are upgraded with the bank ref number from the matching bank debit

## Running tests

```bash
uv run pytest
```
