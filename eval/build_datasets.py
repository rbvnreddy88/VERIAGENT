"""Create the five benchmark CSVs in eval/datasets/.

* retail_sales.csv     synthetic store orders 2021-2023 (seeded, reproducible)
* cars.csv             classic auto MPG data            (vega-datasets)
* seattle_weather.csv  daily Seattle weather 2012-2015  (vega-datasets)
* stocks.csv           monthly prices of 5 tech stocks  (vega-datasets)
* iowa_electricity.csv Iowa net generation by source    (vega-datasets)

Run: python eval/build_datasets.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent / "datasets"


def retail_sales(seed: int = 42, n: int = 3000) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    regions = ["North", "South", "East", "Central"]
    cats = {"Furniture": (420, 0.16), "Technology": (650, 0.24), "Office Supplies": (140, 0.21)}
    dates = pd.to_datetime("2021-01-01") + pd.to_timedelta(rng.integers(0, 365 * 3, n), unit="D")
    region = rng.choice(regions, n, p=[0.28, 0.24, 0.26, 0.22])
    category = rng.choice(list(cats), n, p=[0.3, 0.3, 0.4])
    qty = rng.integers(1, 9, n)
    base = np.array([cats[c][0] for c in category]) * rng.lognormal(0, 0.35, n)
    sales = np.round(base * qty / 3, 2)
    year = dates.year
    # discounts grew in 2023, especially in Central, which drives the profit drop
    disc = rng.choice([0, 0.1, 0.2, 0.3, 0.4, 0.5], n, p=[0.35, 0.25, 0.18, 0.12, 0.06, 0.04])
    bump = (year == 2023) & (rng.random(n) < np.where(region == "Central", 0.55, 0.15))
    disc = np.where(bump, np.minimum(disc + 0.3, 0.6), disc)
    margin = np.array([cats[c][1] for c in category]) - 0.55 * disc + rng.normal(0, 0.04, n)
    profit = np.round(sales * margin, 2)
    df = pd.DataFrame({"order_id": [f"ORD-{i:05d}" for i in range(1, n + 1)],
                       "order_date": dates.strftime("%Y-%m-%d"), "region": region,
                       "category": category, "quantity": qty, "sales": sales,
                       "discount": np.round(disc, 2), "profit": profit})
    return df.sort_values("order_date").reset_index(drop=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    retail_sales().to_csv(OUT / "retail_sales.csv", index=False)
    from vega_datasets import data

    cars = data.cars()
    cars["Year"] = pd.to_datetime(cars["Year"]).dt.year
    cars.to_csv(OUT / "cars.csv", index=False)
    data.seattle_weather().to_csv(OUT / "seattle_weather.csv", index=False)
    data.stocks().to_csv(OUT / "stocks.csv", index=False)
    ie = data.iowa_electricity()
    ie["year"] = pd.to_datetime(ie["year"]).dt.year
    ie.to_csv(OUT / "iowa_electricity.csv", index=False)
    for p in sorted(OUT.glob("*.csv")):
        d = pd.read_csv(p)
        print(f"{p.name:24s} {d.shape[0]:5d} rows  {list(d.columns)}")


if __name__ == "__main__":
    main()
