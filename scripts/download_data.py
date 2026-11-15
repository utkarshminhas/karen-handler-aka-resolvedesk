"""Download the raw datasets into data/ (git-ignored; see licenses in the plan, §3).

    python scripts/download_data.py              # Olist + Bitext
    python scripts/download_data.py --abcd       # also ABCD (optional phrasing source)
    python scripts/download_data.py --force      # re-download even if files exist

Olist comes from Kaggle via kagglehub. If Kaggle asks for credentials, put your token in
~/.kaggle/kaggle.json (or set KAGGLE_USERNAME / KAGGLE_KEY).
"""

import argparse
import shutil
import sys
from pathlib import Path

import httpx
from _common import ABCD_DIR, BITEXT_DIR, OLIST_DIR

OLIST_KAGGLE_ID = "olistbr/brazilian-ecommerce"
OLIST_FILES = [
    "olist_customers_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_products_dataset.csv",
    "product_category_name_translation.csv",
]
BITEXT_HF_ID = "bitext/Bitext-customer-support-llm-chatbot-training-dataset"
ABCD_URL = "https://github.com/asappresearch/abcd/raw/master/data/abcd_v1.1.json.gz"


def download_olist(force: bool) -> None:
    if not force and all((OLIST_DIR / f).exists() for f in OLIST_FILES):
        print(f"olist: already present in {OLIST_DIR}")
        return
    import kagglehub

    print(f"olist: downloading {OLIST_KAGGLE_ID} from Kaggle ...")
    src = Path(kagglehub.dataset_download(OLIST_KAGGLE_ID, force_download=force))
    OLIST_DIR.mkdir(parents=True, exist_ok=True)
    missing = []
    for name in OLIST_FILES:
        hits = list(src.rglob(name))
        if not hits:
            missing.append(name)
            continue
        shutil.copy2(hits[0], OLIST_DIR / name)
    if missing:
        sys.exit(f"olist: files missing from the Kaggle download: {missing}")
    print(f"olist: {len(OLIST_FILES)} files -> {OLIST_DIR}")


def download_bitext(force: bool) -> None:
    out = BITEXT_DIR / "bitext.parquet"
    if out.exists() and not force:
        print(f"bitext: already present at {out}")
        return
    from datasets import load_dataset

    print(f"bitext: downloading {BITEXT_HF_ID} from Hugging Face ...")
    ds = load_dataset(BITEXT_HF_ID, split="train")
    BITEXT_DIR.mkdir(parents=True, exist_ok=True)
    ds.to_parquet(str(out))
    print(f"bitext: {ds.num_rows} rows, {len(set(ds['intent']))} intents -> {out}")


def download_abcd(force: bool) -> None:
    out = ABCD_DIR / "abcd_v1.1.json.gz"
    if out.exists() and not force:
        print(f"abcd: already present at {out}")
        return
    print("abcd: downloading from GitHub ...")
    ABCD_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", ABCD_URL, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            f.writelines(r.iter_bytes())
    print(f"abcd: {out.stat().st_size / 1e6:.1f} MB -> {out}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--abcd", action="store_true", help="also download ABCD (optional)")
    p.add_argument("--skip-olist", action="store_true")
    p.add_argument("--skip-bitext", action="store_true")
    p.add_argument("--force", action="store_true", help="re-download even if present")
    args = p.parse_args()

    if not args.skip_olist:
        download_olist(args.force)
    if not args.skip_bitext:
        download_bitext(args.force)
    if args.abcd:
        download_abcd(args.force)


if __name__ == "__main__":
    main()
