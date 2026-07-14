# Inter-Annotator Agreement Script

Computes Cohen's kappa, quadratic weighted kappa, and percent agreement
between two raters (Lisa & Heather) scoring comprehension questions, reading
directly from a live Google Sheet, and writes the results back into that
same sheet as a new tab.

## What it does

- Reads the `Sheet1` tab of your Google Sheet.
- Compares **Lisa's Score** (column L) vs **Heather's score** (column N).
- Only counts rows where **both** raters gave a numeric score (0/1/2).
  - Rows blank on either side are skipped.
  - Rows scored as text (e.g. `"Connection"` / `"connection"`) are skipped —
    they don't count as agreement or disagreement.
  - If Heather's cell is a formula like `=L3` (meaning "same as Lisa"), the
    script reads the calculated value, so it counts as a normal agreeing
    score.
- Computes agreement **overall** and broken out **by Question Type**
  (Literal / Inferential / Connection / etc.).
- Adds a brand-new tab named `Agreement_<timestamp>` to the live Google
  Sheet with the results and the config used. Nothing existing is
  overwritten, and every run keeps its own tab, so you can compare runs
  over time.

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
     somewhere on your machine (don't commit it to git / share it publicly,
     it grants API access).

3. **Sharing:**
   - If your sheet is set to *"Anyone with the link can edit"*, no extra
     step is needed — the service account can access it via the link.
   - If your sheet is restricted to specific people, open the JSON key
     file, copy the `client_email` value, and share the Google Sheet with
     that email address (Editor access).

## Usage

```bash
python3 compute_agreement.py "<google_sheet_url>" <path_to_service_account.json>
```

Example:
```bash
python3 compute_agreement.py \
  "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit" \
  ./service_account.json
```

You'll see console output like:

```
Total data rows scanned: 162
Rows skipped (blank on one/both sides): 0
Rows skipped (non-numeric, e.g. 'Connection'): 50
Rows used for agreement: 112

=== OVERALL ===
  n: 112
  Cohen's kappa: 0.7433
  Quadratic weighted kappa: 0.8467
  Percent agreement: 0.8393

=== BY QUESTION TYPE ===
  Literal:
    n: 47
    ...

Wrote results into new tab 'Agreement_2026-07-14_16-53-23' in the live Google Sheet.
```

And a new tab will appear in your Google Sheet with the same info plus the
exact config used for that run.

## Customizing for a different sheet layout

Edit the config block near the top of `compute_agreement.py`:

```python
SHEET_NAME = "Sheet1"     # which tab to read
DATA_START_ROW = 3        # first row of actual data
TYPE_COL = "C"            # Question Type column
RATER1_COL = "L"          # Lisa's score column
RATER2_COL = "N"          # Heather's score column
RATER1_NAME = "Lisa"
RATER2_NAME = "Heather"
```

## Troubleshooting

| Error | Likely cause |
|---|---|
| `PERMISSION_DENIED` / `403` | Sheets/Drive API not enabled, or sheet not shared with the service account |
| `SpreadsheetNotFound` | Wrong URL, or sheet not accessible to the service account |
| `WorksheetNotFound: Sheet1` | Your tab has a different name — update `SHEET_NAME` |
| Kappa shows `n/a` for a group | Only one score value appears in that group (e.g. everyone scored 0) — kappa is undefined in that case, percent agreement still applies |
