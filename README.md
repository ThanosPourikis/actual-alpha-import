# actual-alpha-import

Import Alpha Bank Greece transaction CSVs into [Actual Budget](https://actualbudget.org/) using the [actualpy](https://github.com/bvanelli/actualpy) library.

## Features

- Parses Alpha Bank Greece e-banking TSV exports (UTF-8 BOM, tab-separated, Greek number format)
- Deduplicates transactions using the bank reference number, so re-running is safe
- Dry-run mode for previewing parsed transactions without touching Actual
- Accepts a single file, multiple files, or a directory of CSVs

## Installation

```bash
pip install -e .
```

Or install dependencies directly:

```bash
pip install actualpy python-dotenv click
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `ACTUAL_SERVER_URL` | URL to your Actual Budget server |
| `ACTUAL_PASSWORD` | Actual server password |
| `ACTUAL_FILE` | Budget file name or sync ID |
| `ACTUAL_ACCOUNT` | Account name in Actual to import into |
| `ACTUAL_ENCRYPTION_PASSWORD` | Optional: budget encryption password |

## Usage

Import a single file:

```bash
python -m actual_alpha_import transactions.csv
```

Import all CSVs in a directory:

```bash
python -m actual_alpha_import ./exports/
```

Import multiple files:

```bash
python -m actual_alpha_import jan.csv feb.csv mar.csv
```

Preview without importing (dry run):

```bash
python -m actual_alpha_import --dry-run transactions.csv
```

Override the target account:

```bash
python -m actual_alpha_import --account "Alpha Savings" transactions.csv
```

## Alpha Bank CSV Format

The export files from Alpha Bank Greece e-banking are TSV (tab-separated) with:

- UTF-8 BOM encoding
- 6 metadata header rows (IBAN, dates, balances)
- Column header row containing `Α/Α`
- Transaction rows with Greek number formatting (dot for thousands, comma for decimals)
- Sign column: `Χ` = debit (expense), `Π` = credit (income)

## Running Tests

```bash
pip install pytest
pytest
```
