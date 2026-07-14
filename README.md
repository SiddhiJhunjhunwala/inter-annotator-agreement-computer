# Command
``` python3 compute_agreement.py \
  "https://docs.google.com/spreadsheets/d/1vwGiZGsVoB0iBISF1kPSSKmCUTLlRlNJPRMXm9Pcj2E/edit" \
  service_account.json \
  --input-tab "Human_Annotations_AnswerScoring" \
  --output-tab "Inter_Annotator_Agreement" \
  --rater1-col "Lisa's Score" \
  --rater2-col "Heather's score" \
  --type-col "Question Type"```

# Inter-Annotator Agreement Script

Computes Cohen's kappa, quadratic weighted kappa, and percent agreement
between two raters, reading directly from a live Google Sheet, and writes
the results back into that same sheet as a new tab.

Fully generic — the input tab, output tab, and rater/grouping columns are
all passed as command-line arguments and matched **by header text**, not
fixed column letters or a hardcoded sheet. Point it at any sheet with two
score columns and it works.

## What it does

- Reads a tab you specify from your Google Sheet.
- Auto-detects the header row (scans the first 10 rows for one containing
  both rater column headers you named), then finds the two rater columns by
  header text.
- Only counts rows where **both** raters gave a numeric score.
  - Rows blank on either side are skipped.
  - Rows scored as non-numeric text (e.g. `"Connection"`) are skipped —
    they don't count as agreement or disagreement.
  - If a cell is a formula like `=L3` (meaning "same as the other rater"),
    the script reads the calculated value, so it counts as a normal
    agreeing score.
- Computes agreement **overall**, and — if you pass a `--type-col` — broken
  out by that column's categories too.
- Writes the results and the exact config used into the tab named by
  `--output-tab`. If that tab doesn't exist yet, it's created. If it
  already exists (e.g. from a previous run), the new results are
  **appended below the existing content**, separated by a divider line —
  nothing is overwritten, and the tab name stays the same every time, so
  your run history accumulates in one place.

## One-time setup

1. **Install dependencies:**
   ```bash
   pip install gspread google-auth scikit-learn
   ```

2. **Create a Google Cloud service account** (this is how the script
   authenticates to read/write the sheet):
   - Go to [console.cloud.google.com](https://console.cloud.google.com) →
     create or select a project.
   - Enable the **Google Sheets API** and **Google Drive API**
     (APIs & Services → Library → search each → Enable).
   - Go to **IAM & Admin → Service Accounts → Create Service Account**.
     Name it anything (e.g. `agreement-script`).
   - Open the new service account → **Keys** tab → **Add Key → Create new
     key → JSON**. This downloads a `.json` credentials file — save it
     somewhere on your machine (don't commit it to git / share it
     publicly, it grants API access — see `.gitignore`).

3. **Sharing:**
   - If your sheet is set to *"Anyone with the link can edit"*, no extra
     step is needed — the service account can access it via the link.
   - If your sheet is restricted to specific people, open the JSON key
     file, copy the `client_email` value, and share the Google Sheet with
     that email address (Editor access).

## Usage

```bash
python3 compute_agreement.py <google_sheet_url> <service_account.json> \
  --input-tab "<tab to read>" \
  --output-tab "<base name for results tab>" \
  --rater1-col "<header text for rater 1's score column>" \
  --rater2-col "<header text for rater 2's score column>" \
  [--type-col "<header text for a grouping column>"] \
  [--header-row N]
```

### Example

```bash
python3 compute_agreement.py \
  "https://docs.google.com/spreadsheets/d/1vwGiZGsVoB0iBISF1kPSSKmCUTLlRlNJPRMXm9Pcj2E/edit" \
  service_account.json \
  --input-tab "Human_Annotations_AnswerScoring" \
  --output-tab "Inter_Annotator_Agreement" \
  --rater1-col "Lisa's Score" \
  --rater2-col "Heather's score"
```

Add `--type-col "Question Type"` (or whatever your grouping column is
called) to also get a breakdown by category. Add `--header-row 2` if the
script's auto-detection ever picks the wrong row.

### Console output looks like

```
Header row detected: 2
Total data rows scanned: 162
Rows skipped (blank on one/both sides): 0
Rows skipped (non-numeric): 50
Rows used for agreement: 112

=== OVERALL ===
  n: 112
  Cohen's kappa: 0.7433
  Quadratic weighted kappa: 0.8467
  Percent agreement: 0.8393

Appended results to tab 'Inter_Annotator_Agreement' (starting at row 24) in the live Google Sheet.
```

That tab appears (or grows) in your Google Sheet with the same results plus
the exact config used for that run (input tab, columns, header row,
exclusion rules, row counts). Run it again later and the next block gets
appended below the last one, separated by a `====` divider line, rather
than creating a new tab.

## Arguments reference

| Argument | Required? | Description |
|---|---|---|
| `sheet_url` | yes | Full Google Sheets URL (or bare spreadsheet ID) |
| `service_account_json` | yes | Path to your service account credentials file |
| `--input-tab` | yes | Name of the tab/worksheet to read annotations from |
| `--output-tab` | yes | Name of the results tab. Created if it doesn't exist; otherwise new results are appended below existing content |
| `--rater1-col` | yes | Exact header text of rater 1's score column |
| `--rater2-col` | yes | Exact header text of rater 2's score column |
| `--type-col` | no | Header text of a column to group agreement by (e.g. "Question Type") |
| `--header-row` | no | Force which row (1-indexed) has the headers, if auto-detection picks wrong |

## Troubleshooting

| Error | Likely cause |
|---|---|
| `PERMISSION_DENIED` / `403` | Sheets/Drive API not enabled, or sheet not shared with the service account |
| `SpreadsheetNotFound` | Wrong URL, or sheet not accessible to the service account |
| `WorksheetNotFound: <name>` | `--input-tab` doesn't match the real tab name exactly (check spelling/spaces) |
| `Could not find a header row containing both '...' and '...'` | Header text doesn't match exactly — check for typos, extra spaces, or try `--header-row` to force the right row |
| Kappa shows `n/a` for a group | Only one score value appears in that group (e.g. everyone scored 0) — kappa is undefined in that case; percent agreement still applies |