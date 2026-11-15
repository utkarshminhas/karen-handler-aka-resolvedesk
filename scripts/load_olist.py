"""Load the Olist CSVs (data/olist/) into the store zone of Postgres.

    python scripts/load_olist.py            # replaces all store-zone data
    python scripts/load_olist.py --force    # also wipes existing refunds

What the loader does on top of a plain copy (plan v2.1, §3 / §5):
  * Store clock: the store is a snapshot "as of" STORE_NOW. Orders bought after STORE_NOW
    are dropped; deliveries after STORE_NOW are not yet delivered (status -> shipped).
  * Canceled orders are dropped entirely (with their items and payments).
  * Identity: customers are keyed on customer_unique_id (Olist's customer_id is per order).
  * orders.total = sum(item price + freight), falling back to the sum of payments.
  * products: category translated to English, price = median item price. name_en and
    description stay empty here; scripts/curate_catalog.py fills them for the subset.

Connects as the admin role (ADMIN_DATABASE_URL), since this is a one-off load, not the
MCP server.
"""

import argparse
import sys
import time

import pandas as pd
import psycopg
from _common import OLIST_DIR, settings


def read(name: str, **kw) -> pd.DataFrame:
    path = OLIST_DIR / name
    if not path.exists():
        sys.exit(f"missing {path} - run scripts/download_data.py first")
    # utf-8-sig: the category translation CSV ships with a BOM
    return pd.read_csv(path, encoding="utf-8-sig", **kw)


def none_for_nan(df: pd.DataFrame) -> list[tuple]:
    df = df.astype(object).where(df.notna(), None)
    return list(df.itertuples(index=False, name=None))


def build(store_now: pd.Timestamp) -> dict[str, pd.DataFrame]:
    customers = read("olist_customers_dataset.csv")
    orders = read(
        "olist_orders_dataset.csv",
        parse_dates=[
            "order_purchase_timestamp",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ],
    )
    items = read("olist_order_items_dataset.csv")
    payments = read("olist_order_payments_dataset.csv")
    products = read("olist_products_dataset.csv")
    translation = read("product_category_name_translation.csv")

    # --- orders: identity + store clock ---
    orders = orders.merge(customers[["customer_id", "customer_unique_id", "customer_city", "customer_state"]],
                          on="customer_id", how="left")
    n_all = len(orders)
    orders = orders[orders["order_purchase_timestamp"] <= store_now].copy()
    n_future = n_all - len(orders)

    # Canceled orders are removed entirely: Olist has no cancellation date, so their status
    # would leak post-STORE_NOW information, and nothing in the PoC acts on them.
    n_canceled = int((orders["order_status"] == "canceled").sum())
    orders = orders[orders["order_status"] != "canceled"].copy()

    late = orders["order_delivered_customer_date"] > store_now
    orders.loc[late, "order_delivered_customer_date"] = pd.NaT
    orders.loc[late & (orders["order_status"] == "delivered"), "order_status"] = "shipped"

    item_tot = (items.assign(t=items["price"] + items["freight_value"])
                .groupby("order_id")["t"].sum())
    pay_tot = payments.groupby("order_id")["payment_value"].sum()
    orders["total"] = (orders["order_id"].map(item_tot)
                       .fillna(orders["order_id"].map(pay_tot))
                       .fillna(0).round(2))

    kept = set(orders["order_id"])

    customers_store = (orders[["customer_unique_id", "customer_city", "customer_state"]]
                       .drop_duplicates("customer_unique_id"))

    order_rows = orders[["order_id", "customer_unique_id", "order_status", "order_purchase_timestamp",
                         "order_delivered_customer_date", "order_estimated_delivery_date", "total"]]

    item_rows = items[items["order_id"].isin(kept)][
        ["order_id", "order_item_id", "product_id", "price", "freight_value"]]

    pay_rows = payments[payments["order_id"].isin(kept)][
        ["order_id", "payment_sequential", "payment_type", "payment_installments", "payment_value"]]

    # --- products: English category + median price ---
    tr = dict(zip(translation["product_category_name"], translation["product_category_name_english"]))
    products["category"] = products["product_category_name"].map(
        lambda c: tr.get(c, c.replace("_", " ") if isinstance(c, str) else "uncategorized"))
    median_price = items.groupby("product_id")["price"].median().round(2)
    products["price"] = products["product_id"].map(median_price)
    products["name_en"] = None
    products["description"] = None
    product_rows = products[["product_id", "category", "name_en", "description", "price"]]

    print(f"orders: {n_all} in Olist, {n_future} after STORE_NOW dropped, {n_canceled} canceled dropped, "
          f"{int(late.sum())} deliveries after STORE_NOW reset to not-yet-delivered")
    return {
        "customers_store": customers_store,
        "orders": order_rows,
        "products": product_rows,
        "order_items": item_rows,
        "payments": pay_rows,
    }


COPY_SQL = {
    "customers_store": "COPY store.customers_store (customer_unique_id, city, state) FROM STDIN",
    "orders": "COPY store.orders (order_id, customer_unique_id, status, purchase_ts, delivered_ts, "
              "estimated_ts, total) FROM STDIN",
    "products": "COPY store.products (product_id, category, name_en, description, price) FROM STDIN",
    "order_items": "COPY store.order_items (order_id, item_seq, product_id, price, freight) FROM STDIN",
    "payments": "COPY store.payments (order_id, payment_seq, type, installments, value) FROM STDIN",
}


def load(conn: psycopg.Connection, frames: dict[str, pd.DataFrame], force: bool) -> None:
    with conn.transaction():
        n_refunds = conn.execute("SELECT count(*) FROM store.refunds").fetchone()[0]
        if n_refunds and not force:
            sys.exit(f"store.refunds has {n_refunds} rows; re-run with --force to wipe them")
        conn.execute("TRUNCATE store.refunds, store.payments, store.order_items, store.orders, "
                     "store.customers_store, store.products")
        for table in ["customers_store", "orders", "products", "order_items", "payments"]:
            rows = none_for_nan(frames[table])
            t0 = time.perf_counter()
            with conn.cursor().copy(COPY_SQL[table]) as cp:
                for row in rows:
                    cp.write_row(row)
            print(f"  store.{table:<16} {len(rows):>7} rows  ({time.perf_counter() - t0:.1f}s)")


def report(conn: psycopg.Connection, store_now: pd.Timestamp) -> None:
    q = lambda sql, *a: conn.execute(sql, a).fetchall()
    print("\nstatus mix:", ", ".join(f"{s}={n}" for s, n in
                                     q("SELECT status, count(*) FROM store.orders GROUP BY 1 ORDER BY 2 DESC")))
    repeat = q("""SELECT count(*) FILTER (WHERE n >= 2), count(*) FILTER (WHERE n >= 3)
                  FROM (SELECT count(*) n FROM store.orders GROUP BY customer_unique_id) x""")[0]
    print(f"customers with 2+ orders: {repeat[0]}, with 3+: {repeat[1]}  (demo persona pool)")
    recent = q("""SELECT count(*) FROM store.orders
                  WHERE delivered_ts BETWEEN %s::timestamp - interval '30 days' AND %s::timestamp""",
               store_now, store_now)[0][0]
    print(f"orders delivered in the 30 days before STORE_NOW: {recent}  (inside a 30-day return window)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force", action="store_true", help="wipe existing refunds too")
    args = p.parse_args()

    cfg = settings()
    store_now = pd.Timestamp(cfg.store_now)
    print(f"STORE_NOW = {store_now}")
    frames = build(store_now)
    with psycopg.connect(cfg.admin_database_url) as conn:
        load(conn, frames, args.force)
        report(conn, store_now)
    print("\ndone.")


if __name__ == "__main__":
    main()
