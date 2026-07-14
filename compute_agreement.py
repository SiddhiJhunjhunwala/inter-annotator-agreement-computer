"""
compute_agreement.py

Computes inter-annotator agreement (Cohen's kappa, quadratic weighted kappa,
percent agreement) between two raters directly from a Google Sheet, and
writes the results back into the SAME live Google Sheet as a new tab.

Fully generic: input tab, output tab, and rater/type columns are all passed
as arguments (matched by HEADER TEXT, not fixed column letters), so this
works on any sheet with a similar layout without editing the script.

SETUP (one-time):
    pip install gspread google-auth scikit-learn
    -> Enable "Google Sheets API" and "Google Drive API" in a Google Cloud
       project, create a Service Account, download its JSON key.
    -> If the sheet is link-shared ("Anyone with the link can edit"), no
       further sharing needed. Otherwise share the sheet with the service
       account's client_email (Editor access).

USAGE:
    python3 compute_agreement.py <google_sheet_url> <service_account.json> \\
        --input-tab "<tab to read>" \\
        --output-tab "<base name for results tab>" \\
        --rater1-col "<header text for rater 1's score column>" \\
        --rater2-col "<header text for rater 2's score column>" \\
        [--type-col "<header text for a grouping column>"] \\
        [--header-row N]

Example (matches this project's sheet):
    python3 compute_agreement.py \\
        "https://docs.google.com/spreadsheets/d/1vwGiZGsVoB0iBISF1kPSSKmCUTLlRlNJPRMXm9Pcj2E/edit" \\
        service_account.json \\
        --input-tab "Human_Annotations_AnswerScoring" \\
        --output-tab "Inter_Annotator_Agreement" \\
        --rater1-col "Lisa's Score" \\
        --rater2-col "Heather's score"

RULES APPLIED:
    - Only rows where BOTH raters gave a numeric score are used.
    - Rows blank on either side are skipped.
    - Rows scored with non-numeric text (e.g. "Connection") are skipped --
      they do NOT count as agreement or disagreement.
    - A formula cell (e.g. "=L3") is read via its calculated value, so a
      cell meaning "same score as the other rater" counts as a normal
      agreeing numeric score.
    - Agreement is computed overall, and (if --type-col is given and found)
      broken out by that column's categories.
    - Each run appends a NEW tab named "<output-tab>_<timestamp>" -- nothing
      existing is modified or overwritten, so past runs are preserved.
"""

import sys
import re
import argparse
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from sklearn.metrics import cohen_kappa_score

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def parse_args():
    p = argparse.ArgumentParser(description="Compute inter-annotator agreement from a Google Sheet.")
    p.add_argument("sheet_url", help="Google Sheet URL (or bare spreadsheet ID)")
    p.add_argument("service_account_json", help="Path to service account credentials JSON")
    p.add_argument("--input-tab", required=True, help="Name of the tab/worksheet to read annotations from")
    p.add_argument("--output-tab", required=True, help="Base name for the results tab (timestamp is appended)")
    p.add_argument("--rater1-col", required=True, help="Header text of rater 1's score column")
    p.add_argument("--rater2-col", required=True, help="Header text of rater 2's score column")
    p.add_argument("--type-col", default=None, help="Header text of a column to group agreement by (optional)")
    p.add_argument("--header-row", type=int, default=None,
                    help="Row number (1-indexed) containing column headers. "
                         "If omitted, the script auto-detects the row containing both rater column headers.")
    return p.parse_args()


def extract_sheet_id(url_or_id):
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url_or_id)
    return match.group(1) if match else url_or_id.strip()


def is_number(v):
    if v is None:
        return False
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return False
        try:
            float(s)
            return True
        except ValueError:
            return False
    return isinstance(v, (int, float))


def normalize_type(t):
    if t is None or str(t).strip() == "":
        return "(blank)"
    return str(t).strip().title()


def find_header_row_and_cols(all_values, rater1_name, rater2_name, type_name, forced_header_row):
    """Locate the header row (by index, 1-based) and the column indices
    (0-based, for list access) for rater1, rater2, and optionally type,
    matching by header text (case-insensitive, whitespace-trimmed)."""

    def norm(s):
        return str(s).strip().lower()

    r1n, r2n = norm(rater1_name), norm(rater2_name)
    tn = norm(type_name) if type_name else None

    rows_to_check = [forced_header_row] if forced_header_row else range(1, min(len(all_values), 10) + 1)

    for row_num in rows_to_check:
        if row_num - 1 >= len(all_values):
            continue
        row = all_values[row_num - 1]
        norm_row = [norm(c) for c in row]
        if r1n in norm_row and r2n in norm_row:
            c1 = norm_row.index(r1n)
            c2 = norm_row.index(r2n)
            ct = norm_row.index(tn) if (tn and tn in norm_row) else None
            return row_num, c1, c2, ct

    raise ValueError(
        f"Could not find a header row containing both '{rater1_name}' and '{rater2_name}'. "
        f"Checked rows 1-{max(rows_to_check) if not forced_header_row else forced_header_row}. "
        f"Try passing --header-row explicitly, or double-check the column header text."
    )


def load_scores(all_values, header_row, c1, c2, ct):
    rows = []
    for i in range(header_row + 1, len(all_values) + 1):
        row_vals = all_values[i - 1]
        qtype = row_vals[ct] if (ct is not None and ct < len(row_vals)) else None
        s1 = row_vals[c1] if c1 < len(row_vals) else None
        s2 = row_vals[c2] if c2 < len(row_vals) else None
        qtype = qtype if (qtype and str(qtype).strip() != "") else None
        s1 = s1 if (s1 and str(s1).strip() != "") else None
        s2 = s2 if (s2 and str(s2).strip() != "") else None
        if qtype is None and s1 is None and s2 is None:
            continue
        rows.append((i, qtype, s1, s2))
    return rows


def filter_numeric_pairs(rows):
    kept = []
    skipped_blank = 0
    skipped_text = 0
    for r, qtype, s1, s2 in rows:
        if s1 is None or s2 is None:
            skipped_blank += 1
            continue
        if not is_number(s1) or not is_number(s2):
            skipped_text += 1
            continue
        kept.append((r, normalize_type(qtype), float(s1), float(s2)))
    return kept, skipped_blank, skipped_text


def compute_metrics(pairs):
    if len(pairs) < 2:
        return None
    y1 = [p[2] for p in pairs]
    y2 = [p[3] for p in pairs]
    labels = sorted(set(y1) | set(y2))
    if len(labels) < 2:
        kappa = None
        qkappa = None
    else:
        kappa = cohen_kappa_score(y1, y2, labels=labels)
        qkappa = cohen_kappa_score(y1, y2, labels=labels, weights="quadratic")
    pct_agree = sum(1 for a, b in zip(y1, y2) if a == b) / len(y1)
    return {
        "n": len(pairs),
        "cohens_kappa": kappa,
        "quadratic_weighted_kappa": qkappa,
        "percent_agreement": pct_agree,
    }


def fmt(x):
    return "n/a (only one label present)" if x is None else round(x, 4)


def main():
    args = parse_args()

    creds = Credentials.from_service_account_file(args.service_account_json, scopes=SCOPES)
    gc = gspread.authorize(creds)

    sheet_id = extract_sheet_id(args.sheet_url)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(args.input_tab)

    all_values = ws.get_all_values()
    header_row, c1, c2, ct = find_header_row_and_cols(
        all_values, args.rater1_col, args.rater2_col, args.type_col, args.header_row
    )
    if args.type_col and ct is None:
        print(f"Warning: --type-col '{args.type_col}' not found in header row {header_row}; "
              f"skipping by-type breakdown.")

    rows = load_scores(all_values, header_row, c1, c2, ct)
    pairs, skipped_blank, skipped_text = filter_numeric_pairs(rows)
    overall = compute_metrics(pairs)

    by_type = {}
    types_in_order = []
    if ct is not None:
        for r, qtype, s1, s2 in pairs:
            if qtype not in by_type:
                by_type[qtype] = []
                types_in_order.append(qtype)
            by_type[qtype].append((r, qtype, s1, s2))
    type_metrics = {t: compute_metrics(v) for t, v in by_type.items()}

    # ---------------- console output ---------------- #
    print(f"Header row detected: {header_row}")
    print(f"Total data rows scanned: {len(rows)}")
    print(f"Rows skipped (blank on one/both sides): {skipped_blank}")
    print(f"Rows skipped (non-numeric): {skipped_text}")
    print(f"Rows used for agreement: {len(pairs)}\n")

    print("=== OVERALL ===")
    if overall:
        print(f"  n: {overall['n']}")
        print(f"  Cohen's kappa: {fmt(overall['cohens_kappa'])}")
        print(f"  Quadratic weighted kappa: {fmt(overall['quadratic_weighted_kappa'])}")
        print(f"  Percent agreement: {fmt(overall['percent_agreement'])}")
    else:
        print("  Not enough data.")

    if ct is not None:
        print(f"\n=== BY {args.type_col.upper()} ===")
        for t in types_in_order:
            m = type_metrics[t]
            print(f"  {t}:")
            if m:
                print(f"    n: {m['n']}")
                print(f"    Cohen's kappa: {fmt(m['cohens_kappa'])}")
                print(f"    Quadratic weighted kappa: {fmt(m['quadratic_weighted_kappa'])}")
                print(f"    Percent agreement: {fmt(m['percent_agreement'])}")
            else:
                print("    Not enough data.")

    # ---------------- write/append results into the live sheet ---------------- #
    # Always use the SAME tab name (args.output_tab). If it doesn't exist yet,
    # create it. Each run's results are appended below whatever is already
    # there, separated by a divider line, so the full run history accumulates
    # in one place instead of spawning a new tab every time.
    try:
        out_ws = sh.worksheet(args.output_tab)
    except gspread.exceptions.WorksheetNotFound:
        out_ws = sh.add_worksheet(title=args.output_tab, rows=100, cols=6)

    existing_values = out_ws.get_all_values()
    # Trim trailing fully-empty rows so we don't leave a growing gap each run
    while existing_values and all(c.strip() == "" for c in existing_values[-1]):
        existing_values.pop()
    start_row = len(existing_values) + 1

    out_rows = []
    if start_row > 1:
        out_rows.append(["=" * 60])  # visual divider between runs
        start_row += 1
    out_rows.append(["Inter-Annotator Agreement Report"])
    out_rows.append(["Generated:", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    out_rows.append(["Source sheet:", args.sheet_url])
    out_rows.append([])
    out_rows.append(["Config"])
    out_rows.append(["Input tab", args.input_tab])
    out_rows.append(["Header row", header_row])
    out_rows.append(["Rater 1 column", args.rater1_col])
    out_rows.append(["Rater 2 column", args.rater2_col])
    out_rows.append(["Type/grouping column", args.type_col or "(none)"])
    out_rows.append(["Row inclusion rule", "Both raters must have a numeric score"])
    out_rows.append(["Exclusion rule", "Blank rows and non-numeric scores skipped"])
    out_rows.append(["Formula handling", "Formula cells (e.g. '=L3') read via calculated value"])
    out_rows.append(["Metrics computed", "Cohen's kappa, quadratic weighted kappa, percent agreement"])
    out_rows.append([])
    out_rows.append(["Row counts"])
    out_rows.append(["Total data rows scanned", len(rows)])
    out_rows.append(["Skipped - blank on one/both sides", skipped_blank])
    out_rows.append(["Skipped - non-numeric", skipped_text])
    out_rows.append(["Rows used for agreement", len(pairs)])
    out_rows.append([])
    out_rows.append(["Results"])
    out_rows.append(["Group", "n", "Cohen's kappa", "Quadratic weighted kappa", "Percent agreement"])

    def metric_row(label, m):
        if m:
            return [label, m["n"], fmt(m["cohens_kappa"]), fmt(m["quadratic_weighted_kappa"]), fmt(m["percent_agreement"])]
        return [label, "not enough data", "", "", ""]

    out_rows.append(metric_row("Overall", overall))
    for t in types_in_order:
        out_rows.append(metric_row(t, type_metrics[t]))

    rows_needed = start_row + len(out_rows) - 1
    if rows_needed > out_ws.row_count:
        out_ws.add_rows(rows_needed - out_ws.row_count + 20)  # pad a bit extra for the next run

    out_ws.update(range_name=f"A{start_row}", values=out_rows, value_input_option="RAW")
    print(f"\nAppended results to tab '{args.output_tab}' (starting at row {start_row}) in the live Google Sheet.")


if __name__ == "__main__":
    main()