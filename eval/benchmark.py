"""50 analytical tasks over 5 datasets, each with ground-truth answers computed in pandas.

Each task:
  id        unique id
  dataset   CSV name in eval/datasets
  question  natural-language goal given to the system
  answers   function(df) -> {name: value}   key numbers a correct report must contain
  chart     optional {"types": allowed chart types,
                      "ref": function(df) -> DataFrame[x, y]  (values a correct chart shows)
                      or "cols": [x, y] for scatter (column check only)}
`df` is loaded with veriagent.profiling.load_csv, so date columns are datetime64.
"""
from __future__ import annotations

import pandas as pd


def _yr(df, col):
    return df[col].dt.year


def _pct(a, b):
    return (b - a) / abs(a) * 100


TASKS: list[dict] = []


def task(id, dataset, question, answers, chart=None):
    TASKS.append({"id": id, "dataset": dataset, "question": question, "answers": answers, "chart": chart})


# --------------------------------------------------------------------------- retail
R = "retail_sales.csv"
task("R01", R, "What were the total sales across all orders?",
     lambda d: {"total_sales": d.sales.sum()})
task("R02", R, "How did total profit change from 2022 to 2023, in absolute terms and percent?",
     lambda d: (lambda g: {"profit_2022": g[2022], "profit_2023": g[2023],
                           "pct_change": _pct(g[2022], g[2023])})(d.groupby(_yr(d, "order_date")).profit.sum()),
     {"types": ["line", "bar"], "ref": lambda d: d.groupby(_yr(d, "order_date")).profit.sum().reset_index()})
task("R03", R, "Which region had the lowest total profit in 2023, and how much was it?",
     lambda d: {"lowest_region_profit_2023": d[_yr(d, "order_date") == 2023].groupby("region").profit.sum().min()},
     {"types": ["bar", "barh"],
      "ref": lambda d: d[_yr(d, "order_date") == 2023].groupby("region").profit.sum().reset_index()})
task("R04", R, "Compare the average discount in 2022 and 2023.",
     lambda d: (lambda g: {"avg_discount_2022": g[2022], "avg_discount_2023": g[2023]})(
         d.groupby(_yr(d, "order_date")).discount.mean()))
task("R05", R, "Which product category has the highest total sales, and what is that total?",
     lambda d: {"top_category_sales": d.groupby("category").sales.sum().max()},
     {"types": ["bar", "barh"], "ref": lambda d: d.groupby("category").sales.sum().reset_index()})
task("R06", R, "What is the overall profit margin (total profit as a percentage of total sales)?",
     lambda d: {"profit_margin_pct": d.profit.sum() / d.sales.sum() * 100})
task("R07", R, "What is the correlation between discount and profit?",
     lambda d: {"corr_discount_profit": d.discount.corr(d.profit)})
task("R08", R, "In 2023, how many orders had a discount of 40% or more, and what was their total profit?",
     lambda d: (lambda x: {"n_orders": len(x), "total_profit": x.profit.sum()})(
         d[(_yr(d, "order_date") == 2023) & (d.discount >= 0.4)]))
task("R09", R, "What is the average sales value per order in each region? Which region is highest?",
     lambda d: {"highest_avg_order_sales": d.groupby("region").sales.mean().max()},
     {"types": ["bar", "barh"], "ref": lambda d: d.groupby("region").sales.mean().reset_index()})
task("R10", R, "Show monthly total sales in 2023. Which month had the highest sales and how much?",
     lambda d: {"max_month_sales_2023": d[_yr(d, "order_date") == 2023].groupby(
         d.order_date.dt.month).sales.sum().max()},
     {"types": ["line", "bar"], "ref": lambda d: d[_yr(d, "order_date") == 2023].groupby(
         d.order_date.dt.month).sales.sum().reset_index()})

# --------------------------------------------------------------------------- cars
C = "cars.csv"
task("C01", C, "What is the average miles per gallon of all cars?",
     lambda d: {"avg_mpg": d.Miles_per_Gallon.mean()})
task("C02", C, "Compare average miles per gallon across origins.",
     lambda d: (lambda g: {"mpg_usa": g["USA"], "mpg_europe": g["Europe"], "mpg_japan": g["Japan"]})(
         d.groupby("Origin").Miles_per_Gallon.mean()),
     {"types": ["bar", "barh"], "ref": lambda d: d.groupby("Origin").Miles_per_Gallon.mean().reset_index()})
task("C03", C, "How did average miles per gallon change from 1970 to 1982?",
     lambda d: (lambda g: {"mpg_1970": g[1970], "mpg_1982": g[1982], "pct_change": _pct(g[1970], g[1982])})(
         d.groupby("Year").Miles_per_Gallon.mean()),
     {"types": ["line", "bar"], "ref": lambda d: d.groupby("Year").Miles_per_Gallon.mean().reset_index()})
task("C04", C, "What is the correlation between car weight and miles per gallon?",
     lambda d: {"corr_weight_mpg": d.Weight_in_lbs.corr(d.Miles_per_Gallon)},
     {"types": ["scatter"], "cols": ["Weight_in_lbs", "Miles_per_Gallon"]})
task("C05", C, "How many cars have 8 cylinders, and what is their average horsepower?",
     lambda d: {"n_8cyl": int((d.Cylinders == 8).sum()), "avg_hp_8cyl": d[d.Cylinders == 8].Horsepower.mean()})
task("C06", C, "What is the highest horsepower of any car in the data?",
     lambda d: {"max_hp": d.Horsepower.max()})
task("C07", C, "Compare average acceleration by number of cylinders.",
     lambda d: {"accel_4cyl": d[d.Cylinders == 4].Acceleration.mean(),
                "accel_8cyl": d[d.Cylinders == 8].Acceleration.mean()},
     {"types": ["bar", "barh", "line"], "ref": lambda d: d.groupby("Cylinders").Acceleration.mean().reset_index()})
task("C08", C, "What percentage of cars come from Japan?",
     lambda d: {"share_japan_pct": (d.Origin == "Japan").mean() * 100})
task("C09", C, "What is the median weight of cars from the USA?",
     lambda d: {"median_weight_usa": d[d.Origin == "USA"].Weight_in_lbs.median()})
task("C10", C, "How did average horsepower change between 1970 and 1982?",
     lambda d: (lambda g: {"hp_1970": g[1970], "hp_1982": g[1982], "pct_change": _pct(g[1970], g[1982])})(
         d.groupby("Year").Horsepower.mean()))

# --------------------------------------------------------------------------- seattle weather
S = "seattle_weather.csv"
task("S01", S, "What was the total precipitation in Seattle in 2015?",
     lambda d: {"precip_2015": d[_yr(d, "date") == 2015].precipitation.sum()})
task("S02", S, "How did the average maximum temperature change from 2012 to 2015?",
     lambda d: (lambda g: {"tmax_2012": g[2012], "tmax_2015": g[2015]})(d.groupby(_yr(d, "date")).temp_max.mean()),
     {"types": ["line", "bar"], "ref": lambda d: d.groupby(_yr(d, "date")).temp_max.mean().reset_index()})
task("S03", S, "How many days of each weather type were recorded? Which is most common?",
     lambda d: {"rain_days": int((d.weather == "rain").sum()), "sun_days": int((d.weather == "sun").sum())},
     {"types": ["bar", "barh"], "ref": lambda d: d.weather.value_counts().rename_axis("weather").reset_index()})
task("S04", S, "Which month of 2014 had the most precipitation, and how much?",
     lambda d: {"max_month_precip_2014": d[_yr(d, "date") == 2014].groupby(d.date.dt.month).precipitation.sum().max()})
task("S05", S, "What was the highest maximum temperature recorded?",
     lambda d: {"max_temp": d.temp_max.max()})
task("S06", S, "What is the average wind speed?",
     lambda d: {"avg_wind": d.wind.mean()})
task("S07", S, "What is the correlation between daily maximum and minimum temperature?",
     lambda d: {"corr_tmax_tmin": d.temp_max.corr(d.temp_min)})
task("S08", S, "On what percentage of days was there any precipitation?",
     lambda d: {"pct_days_precip": (d.precipitation > 0).mean() * 100})
task("S09", S, "What is the average daily temperature range (max minus min)?",
     lambda d: {"avg_range": (d.temp_max - d.temp_min).mean()})
task("S10", S, "Show the average maximum temperature for each month of 2015. What was it in July?",
     lambda d: {"tmax_july_2015": d[(_yr(d, "date") == 2015) & (d.date.dt.month == 7)].temp_max.mean()},
     {"types": ["line", "bar"], "ref": lambda d: d[_yr(d, "date") == 2015].groupby(
         d.date.dt.month).temp_max.mean().reset_index()})

# --------------------------------------------------------------------------- stocks
K = "stocks.csv"
task("K01", K, "Compare the average price of each stock over the whole period. Which is highest?",
     lambda d: {"highest_avg_price": d.groupby("symbol").price.mean().max()},
     {"types": ["bar", "barh"], "ref": lambda d: d.groupby("symbol").price.mean().reset_index()})
task("K02", K, "How much did AAPL's price change from its first to its last observation, in percent?",
     lambda d: (lambda x: {"aapl_first": x.price.iloc[0], "aapl_last": x.price.iloc[-1],
                           "pct_change": _pct(x.price.iloc[0], x.price.iloc[-1])})(
         d[d.symbol == "AAPL"].sort_values("date")))
task("K03", K, "What was the highest price recorded for AMZN?",
     lambda d: {"amzn_max": d[d.symbol == "AMZN"].price.max()})
task("K04", K, "What was the average MSFT price in 2008?",
     lambda d: {"msft_2008": d[(d.symbol == "MSFT") & (_yr(d, "date") == 2008)].price.mean()})
task("K05", K, "Show IBM's average price per year. What was it in 2000 and in 2009?",
     lambda d: (lambda g: {"ibm_2000": g[2000], "ibm_2009": g[2009]})(
         d[d.symbol == "IBM"].groupby(_yr(d, "date")).price.mean()),
     {"types": ["line", "bar"], "ref": lambda d: d[d.symbol == "IBM"].groupby(_yr(d, "date")).price.mean().reset_index()})
task("K06", K, "Which stock had the highest price on the last date in the data, and what was it?",
     lambda d: {"top_last_price": d[d.date == d.date.max()].price.max()})
task("K07", K, "What is the correlation between AAPL and MSFT monthly prices?",
     lambda d: {"corr_aapl_msft": d.pivot_table(index="date", columns="symbol", values="price")
                .dropna()[["AAPL", "MSFT"]].corr().iloc[0, 1]})
task("K08", K, "In how many months was AAPL's price above 100?",
     lambda d: {"months_above_100": int(((d.symbol == "AAPL") & (d.price > 100)).sum())})
task("K09", K, "By what percentage did GOOG's average price change from 2007 to 2008?",
     lambda d: (lambda g: {"goog_2007": g[2007], "goog_2008": g[2008], "pct_change": _pct(g[2007], g[2008])})(
         d[d.symbol == "GOOG"].groupby(_yr(d, "date")).price.mean()))
task("K10", K, "What was IBM's lowest price?",
     lambda d: {"ibm_min": d[d.symbol == "IBM"].price.min()})

# --------------------------------------------------------------------------- iowa electricity
I = "iowa_electricity.csv"
task("I01", I, "What was the total net generation in 2017?",
     lambda d: {"total_2017": d[d.year == 2017].net_generation.sum()})
task("I02", I, "How did renewable generation change from 2001 to 2017?",
     lambda d: (lambda g: {"ren_2001": g[2001], "ren_2017": g[2017], "pct_change": _pct(g[2001], g[2017])})(
         d[d.source == "Renewables"].set_index("year").net_generation),
     {"types": ["line", "bar"], "ref": lambda d: d[d.source == "Renewables"][["year", "net_generation"]]})
task("I03", I, "What share of total generation came from renewables in 2017?",
     lambda d: {"ren_share_2017_pct": d[(d.year == 2017) & (d.source == "Renewables")].net_generation.sum()
                / d[d.year == 2017].net_generation.sum() * 100})
task("I04", I, "Compare net generation by source in 2017.",
     lambda d: (lambda g: {"fossil_2017": g["Fossil Fuels"], "nuclear_2017": g["Nuclear Energy"],
                           "ren_2017": g["Renewables"]})(d[d.year == 2017].set_index("source").net_generation),
     {"types": ["bar", "barh"], "ref": lambda d: d[d.year == 2017][["source", "net_generation"]]})
task("I05", I, "What was the average yearly generation from fossil fuels?",
     lambda d: {"avg_fossil": d[d.source == "Fossil Fuels"].net_generation.mean()})
task("I06", I, "Which year had the highest total generation, and how much?",
     lambda d: {"max_total": d.groupby("year").net_generation.sum().max()})
task("I07", I, "How much nuclear energy was generated in 2010?",
     lambda d: {"nuclear_2010": d[(d.year == 2010) & (d.source == "Nuclear Energy")].net_generation.sum()})
task("I08", I, "How many times larger was renewable generation in 2017 than in 2001?",
     lambda d: (lambda g: {"ratio": g[2017] / g[2001]})(d[d.source == "Renewables"].set_index("year").net_generation))
task("I09", I, "By what percentage did fossil fuel generation change from 2008 to 2017?",
     lambda d: (lambda g: {"pct_change": _pct(g[2008], g[2017])})(
         d[d.source == "Fossil Fuels"].set_index("year").net_generation))
task("I10", I, "Show total generation per year. What was the total in 2001?",
     lambda d: {"total_2001": d[d.year == 2001].net_generation.sum()},
     {"types": ["line", "bar"], "ref": lambda d: d.groupby("year").net_generation.sum().reset_index()})

assert len(TASKS) == 50 and len({t["id"] for t in TASKS}) == 50
