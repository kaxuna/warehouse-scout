"""One-line descriptions of the 24 TPC-DS tables.

Applied to Oracle as table comments, so the schema carries the kind of
documentation a real warehouse has. Later these comments are part of what
the agent retrieves when it searches the schema.
"""

TPCDS_TABLE_COMMENTS: dict[str, str] = {
    # Fact tables
    "store_sales": "Fact: one row per item sold in a physical store.",
    "store_returns": "Fact: one row per item returned to a physical store.",
    "catalog_sales": "Fact: one row per item sold through the mail-order catalog.",
    "catalog_returns": "Fact: one row per item returned from a catalog order.",
    "web_sales": "Fact: one row per item sold through the web site.",
    "web_returns": "Fact: one row per item returned from a web order.",
    "inventory": "Fact: weekly quantity on hand per item and warehouse.",
    # Dimensions
    "date_dim": "Dimension: one row per calendar day with year, quarter, month and flags.",
    "time_dim": "Dimension: one row per second of the day with hour, minute and shift.",
    "item": "Dimension: products with brand, class, category and prices.",
    "customer": "Dimension: customers with name, birth date and current address key.",
    "customer_address": "Dimension: street addresses with city, county, state and zip.",
    "customer_demographics": "Dimension: gender, marital status, education and credit rating.",
    "household_demographics": "Dimension: household income band, dependants and vehicles.",
    "income_band": "Dimension: lower and upper bound of each income band.",
    "store": "Dimension: physical stores with location, manager and floor space.",
    "warehouse": "Dimension: distribution warehouses with location and size.",
    "call_center": "Dimension: call centers that take catalog orders.",
    "catalog_page": "Dimension: pages of the mail-order catalog.",
    "web_site": "Dimension: web sites that take orders.",
    "web_page": "Dimension: pages of the web sites.",
    "promotion": "Dimension: promotions with channels used and cost.",
    "reason": "Dimension: reasons customers give for a return.",
    "ship_mode": "Dimension: shipping methods and carriers.",
}
