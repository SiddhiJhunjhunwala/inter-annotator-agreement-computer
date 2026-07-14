"""
compute_agreement.py

Computes inter-annotator agreement (Cohen's kappa, quadratic weighted kappa,
percent agreement) between two raters directly from a Google Sheet, and
writes the results back into the SAME live Google Sheet as a new,
timestamped tab.

SETUP (one-time):
    1. pip install gspread google-auth
    2. Create a Google Cloud project -> enable "Google Sheets API" and
       "Google Drive API".
    3. Create a Service Account -> create a JSON key -> download it.
    4. If your sheet is link-shared ("Anyone with the link can edit"), no
       further sharing is needed. If it's restricted to specific people,
       share the sheet with the service account's email address (found
       inside the JSON key file, field "client_email") and give it Editor
       access.

USAGE:
    python3 compute_agreement.py <google_sheet_url> <path_to_service_account.json>

Example:
    python3 compute_agreement.py \\
        "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit" \\
        ./service_account.json

CONFIG (edit these if your sheet layout differs):
    SHEET_NAME       - worksheet/tab holding the annotations
    DATA_START_ROW   - first row of actual data (1-indexed, matches Sheets UI)
    TYPE_COL         - column letter for "Question Type"
    RATER1_COL       - column letter for rater 1's score (e.g. Lisa)
    RATER2_COL       - column letter for rater 2's score (e.g. Heather)
    RATER1_NAME/RATER2_NAME - display names used in the output tab

RULES APPLIED:
    - Only rows where BOTH raters gave a numeric score are used.
    - Rows that are blank on either side are skipped.
    - Rows scored with text (e.g. "Connection"/"connection", used when a
      question wasn't numerically scored) are skipped entirely -- they do
      NOT count as agreement or disagreement.
    - Rater 2's cell may contain a formula like "=L3" meaning "same score as
      rater 1" -- gspread reads the CALCULATED value by default, so this
      resolves to a normal number equal to rater 1's and counts normally.
    - Agreement is computed overall, and separately broken out by Question
      Type.
    - Nothing in the original tabs is modified. A brand-new tab is appended
      each run, named "Agreement_<timestamp>", so past runs are preserved.
"""

import sys
import re
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import a1_range_to_grid_range
from sklearn.metrics import cohen_kappa_score

# ------------------------- CONFIG ------------------------- #
SHEET_NAME = "Sheet1"
DATA_START_ROW = 3
TYPE_COL = "C"
RATER1_COL = "L"
RATER2_COL = "N"
RATER1_NAME = "Lisa"
RATER2_NAME = "Heather"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
# ------------------------------------------------------------ #


def col_letter_to_index(letter):
    """A -> 1, B -> 2, ... (1-indexed, matches gspread/Sheets convention)."""
    idx = 0
    for ch in letter:
        idx = idx * 26 + (ord(ch.upper()) - ord("A") + 1)
    return idx


def extract_sheet_id(url_or_id):
    """Accept a full Google Sheets URL or a bare spreadsheet ID."""
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url_or_id)
    if match:
        return match.group(1)
    return url_or_id.strip()


def is_number(v):
    if v is None:
        return False
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        s = v.strip()
        try:
            float(s)
            return True
        except ValueError:
            return False
    return False


def to_float(v):
    return float(v) if not isinstance(v, float) else v


def normalize_type(t):
    if t is None or str(t).strip() == "":
        return "(blank)"
    return str(t).strip().title()


def load_scores(ws):
    """Read (row, question_type, rater1_score, rater2_score) for every data
    row. gspread's get_all_values() returns calculated values for formula
    cells (e.g. '=L3' resolves to the number it evaluates to)."""
    all_values = ws.get_all_values()  # list of rows, each a list of strings
    c1 = col_letter_to_index(RATER1_COL) - 1  # 0-indexed for list access
    c2 = col_letter_to_index(RATER2_COL) - 1
    ct = col_letter_to_index(TYPE_COL) - 1

    rows = []
    for i, row_vals in enumerate(all_values, start=1):
        if i < DATA_START_ROW:
            continue
        qtype = row_vals[ct] if ct < len(row_vals) else ""
        s1 = row_vals[c1] if c1 < len(row_vals) else ""
        s2 = row_vals[c2] if c2 < len(row_vals) else ""
        qtype = qtype if qtype.strip() != "" else None
        s1 = s1 if s1.strip() != "" else None
        s2 = s2 if s2.strip() != "" else None
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
    if x is None:
        return "n/a (only one label present)"
    return round(x, 4)


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 compute_agreement.py <google_sheet_url> <service_account.json>")
        sys.exit(1)

    sheet_url_or_id = sys.argv[1]
    creds_path = sys.argv[2]

    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    gc = gspread.authorize(creds)

    sheet_id = extract_sheet_id(sheet_url_or_id)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(SHEET_NAME)

    rows = load_scores(ws)
    pairs, skipped_blank, skipped_text = filter_numeric_pairs(rows)

    overall = compute_metrics(pairs)

    by_type = {}
    types_in_order = []
    for r, qtype, s1, s2 in pairs:
        if qtype not in by_type:
            by_type[qtype] = []
            types_in_order.append(qtype)
        by_type[qtype].append((r, qtype, s1, s2))
    type_metrics = {t: compute_metrics(v) for t, v in by_type.items()}

    # ---------------- print to console ---------------- #
    print(f"Total data rows scanned: {len(rows)}")
    print(f"Rows skipped (blank on one/both sides): {skipped_blank}")
    print(f"Rows skipped (non-numeric, e.g. 'Connection'): {skipped_text}")
    print(f"Rows used for agreement: {len(pairs)}\n")

    print("=== OVERALL ===")
    if overall:
        print(f"  n: {overall['n']}")
        print(f"  Cohen's kappa: {fmt(overall['cohens_kappa'])}")
        print(f"  Quadratic weighted kappa: {fmt(overall['quadratic_weighted_kappa'])}")
        print(f"  Percent agreement: {fmt(overall['percent_agreement'])}")
    else:
        print("  Not enough data.")

    print("\n=== BY QUESTION TYPE ===")
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

    # ---------------- write results tab back into the live sheet ---------------- #
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    tab_title = f"Agreement_{timestamp}"[:100]  # Sheets tab name limit is generous
    new_ws = sh.add_worksheet(title=tab_title, rows=100, cols=6)

    out_rows = []
    out_rows.append(["Inter-Annotator Agreement Report"])
    out_rows.append(["Generated:", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    out_rows.append(["Source sheet:", sheet_url_or_id])
    out_rows.append([])
    out_rows.append(["Config"])
    out_rows.append(["Tab analyzed", SHEET_NAME])
    out_rows.append(["Question Type column", TYPE_COL])
    out_rows.append([f"{RATER1_NAME}'s score column", RATER1_COL])
    out_rows.append([f"{RATER2_NAME}'s score column", RATER2_COL])
    out_rows.append(["Data start row", DATA_START_ROW])
    out_rows.append(["Row inclusion rule", "Both raters must have a numeric score"])
    out_rows.append(["Exclusion rule", "Blank rows and text scores (e.g. 'Connection') skipped"])
    out_rows.append(["Formula handling", "Rater 2 formulas (e.g. '=L3') read via calculated value"])
    out_rows.append(["Metrics computed", "Cohen's kappa (unweighted), quadratic weighted kappa, percent agreement"])
    out_rows.append([])
    out_rows.append(["Row counts"])
    out_rows.append(["Total data rows scanned", len(rows)])
    out_rows.append(["Skipped - blank on one/both sides", skipped_blank])
    out_rows.append(["Skipped - non-numeric (e.g. Connection)", skipped_text])
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

    new_ws.update(range_name="A1", values=out_rows, value_input_option="USER_ENTERED")

    print(f"\nWrote results into new tab '{tab_title}' in the live Google Sheet.")


if __name__ == "__main__":
    main()
