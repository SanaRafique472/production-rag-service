"""Check the LLM judge against your own labels (Cohen's kappa).

1. Copy human_labels_to_fill.csv (20 answers from the first live run) to human_labels.csv.
2. Read each answer in the run's JSON and fill human_correct with 1 (correct) or 0.
3. Run:  python -m evals.agreement evals/results/<run>.json
A kappa above ~0.6 means the judge's correctness scores can be trusted for this set.
"""

import csv
import json
import sys
from pathlib import Path

from evals.metrics import cohen_kappa

ROOT = Path(__file__).resolve().parent


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    run = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    judged = {(r["id"], name): r.get("correctness") for name, res in run["results"].items() for r in res["rows"]}
    labels_path = ROOT / "human_labels.csv"
    if not labels_path.exists():
        sys.exit("evals/human_labels.csv not found - copy the template and add your labels first.")
    human, judge = [], []
    with labels_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            score = judged.get((row["question_id"], row["config"]))
            if score is None or row["human_correct"] == "":
                continue
            human.append(int(row["human_correct"]))
            judge.append(1 if score >= 0.75 else 0)  # judge score 4-5 of 5 counts as correct
    if not human:
        sys.exit("No overlapping labelled rows with judge scores.")
    print(
        f"pairs={len(human)}  agreement={sum(h == j for h, j in zip(human, judge, strict=True)) / len(human):.2f}  "
        f"kappa={cohen_kappa(human, judge):.2f}"
    )


if __name__ == "__main__":
    main()
