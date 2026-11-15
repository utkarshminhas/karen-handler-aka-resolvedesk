"""Build the triage eval set from Bitext: a stratified sample mapped to ResolveDesk labels.

    python scripts/prepare_bitext.py                 # 40 per intent (~1,080 rows)
    python scripts/prepare_bitext.py --per-intent 20

Reads data/bitext/bitext.parquet and evals/datasets/intent_map.json, writes
evals/datasets/bitext_sample.jsonl. Not loaded into Postgres: it's eval input, not store data.
Fails loudly if Bitext contains an intent the map doesn't cover.
"""

import argparse
import json
import sys

import pandas as pd
from _common import BITEXT_DIR, EVAL_DATASETS_DIR

SEED = 42


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--per-intent", type=int, default=40)
    args = p.parse_args()

    src = BITEXT_DIR / "bitext.parquet"
    if not src.exists():
        sys.exit(f"missing {src} - run scripts/download_data.py first")
    df = pd.read_parquet(src)
    intent_map = json.loads((EVAL_DATASETS_DIR / "intent_map.json").read_text())["map"]

    found = set(df["intent"])
    unmapped, unused = found - intent_map.keys(), intent_map.keys() - found
    if unmapped:
        sys.exit(f"intents missing from intent_map.json: {sorted(unmapped)}")
    if unused:
        print(f"warning: intent_map.json has intents not in Bitext: {sorted(unused)}")

    n = min(args.per_intent, int(df["intent"].value_counts().min()))
    sample = df.groupby("intent").sample(n=n, random_state=SEED)
    sample = sample.sample(frac=1, random_state=SEED)  # shuffle across intents

    out = EVAL_DATASETS_DIR / "bitext_sample.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for i, r in enumerate(sample.itertuples(index=False)):
            f.write(json.dumps({
                "id": f"bitext-{i:04d}",
                "text": r.instruction,
                "bitext_intent": r.intent,
                "label": intent_map[r.intent],
            }, ensure_ascii=False) + "\n")

    counts = sample["intent"].map(intent_map).value_counts()
    print(f"{len(sample)} rows -> {out}")
    print("label mix:", ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
