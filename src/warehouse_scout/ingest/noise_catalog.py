"""The mess a real warehouse accumulates, described as data.

A clean 24-table benchmark is too easy: every table matters and nothing is
duplicated. Real warehouses carry backups nobody deleted, staging tables,
abandoned experiments and three functions that compute the same thing.
This module declares that mess. `noise_tables.py` creates it in Oracle.

Every object carries its ground truth:
    status        "active" (the query log uses it) or "dead" (nothing does)
    duplicate_of  the object it copies, if any

`build_manifest()` turns that into the answer key the evals score against:
the agent is asked which tables are dead or duplicated, and this is what
"correct" means. Nothing here touches a database.
"""

from dataclasses import dataclass
from itertools import product
from typing import Literal

Status = Literal["active", "dead"]
Kind = Literal["backup", "staging", "duplicate", "deprecated", "scratch", "domain"]


@dataclass(frozen=True)
class NoiseTable:
    name: str
    kind: Kind
    status: Status
    select_sql: str  # the table is created as: CREATE TABLE <name> AS <select_sql>
    comment: str
    duplicate_of: str | None = None


@dataclass(frozen=True)
class NoiseView:
    name: str
    status: Status
    select_sql: str
    comment: str


@dataclass(frozen=True)
class NoiseFunction:
    name: str
    status: Status
    group: str | None  # functions sharing a group compute the same thing
    signature: str  # parameter list and return type
    body: str  # the expression after RETURN


# --- Tables --------------------------------------------------------------------

_BACKUP_SOURCES = (
    "item",
    "customer",
    "customer_address",
    "store",
    "promotion",
    "warehouse",
    "call_center",
    "web_site",
    "household_demographics",
    "ship_mode",
    "reason",
    "income_band",
)
_BACKUP_DATES = ("20230131", "20230630", "20231231")

_FACT_TABLES = (
    "store_sales",
    "web_sales",
    "catalog_sales",
    "store_returns",
    "web_returns",
    "catalog_returns",
    "inventory",
)

_SCRATCH_USERS = ("jsmith", "mlee", "apatel", "kwong")
_SCRATCH_TOPICS = {
    "top_items": (
        "SELECT ss_item_sk, SUM(ss_net_paid) AS revenue FROM store_sales "
        "GROUP BY ss_item_sk ORDER BY revenue DESC FETCH FIRST 100 ROWS ONLY"
    ),
    "cust_sample": "SELECT * FROM customer WHERE ROWNUM <= 200",
    "sales_check": (
        "SELECT ss_sold_date_sk, COUNT(*) AS row_count FROM store_sales GROUP BY ss_sold_date_sk"
    ),
    "returns_debug": "SELECT * FROM store_returns WHERE ROWNUM <= 300",
    "q4_adhoc": (
        "SELECT d_year, d_moy, SUM(ws_ext_sales_price) AS sales FROM web_sales "
        "JOIN date_dim ON ws_sold_date_sk = d_date_sk WHERE d_qoy = 4 GROUP BY d_year, d_moy"
    ),
}

_THROWAWAY_NAMES = (
    "test_table_1",
    "test_table_2",
    "test_table_3",
    "test_table_4",
    "test_table_5",
    "zz_delete_me",
    "foo",
    "bar_backup",
)


def _backups() -> list[NoiseTable]:
    return [
        NoiseTable(
            name=f"{source}_bak_{date}",
            kind="backup",
            status="dead",
            select_sql=f"SELECT * FROM {source}",
            comment=f"Backup of {source} taken {date}.",
            duplicate_of=source,
        )
        for source, date in product(_BACKUP_SOURCES, _BACKUP_DATES)
    ]


def _staging() -> list[NoiseTable]:
    return [
        NoiseTable(
            name=f"stg_{fact}",
            kind="staging",
            status="active",
            select_sql=f"SELECT * FROM {fact} WHERE ROWNUM <= 500",
            comment=f"Landing table for the nightly {fact} load. Truncated each run.",
        )
        for fact in _FACT_TABLES
    ]


def _duplicates() -> list[NoiseTable]:
    # (name, source, status, comment). The first four are copies a second team
    # queries instead of the original: duplicates that are still in use.
    specs: list[tuple[str, str, Status, str]] = [
        ("dim_customer", "customer", "active", "BI team's copy of customer, refreshed weekly."),
        ("dim_item", "item", "active", "BI team's copy of item, refreshed weekly."),
        ("dim_store", "store", "active", "BI team's copy of store, refreshed weekly."),
        ("dim_date", "date_dim", "active", "BI team's copy of date_dim, refreshed weekly."),
        ("customer_copy", "customer", "dead", "Copy of customer. Purpose unknown."),
        ("item2", "item", "dead", "Copy of item. Purpose unknown."),
    ]
    return [
        NoiseTable(
            name=name,
            kind="duplicate",
            status=status,
            select_sql=f"SELECT * FROM {source}",
            comment=comment,
            duplicate_of=source,
        )
        for name, source, status, comment in specs
    ]


def _deprecated() -> list[NoiseTable]:
    specs = [
        (
            "old_customer_v1",
            "SELECT c_customer_sk AS cust_id, c_first_name AS fname, c_last_name AS lname, "
            "c_email_address AS email FROM customer",
            "Customer table from the previous schema version. Replaced by customer.",
        ),
        (
            "old_item_v1",
            "SELECT i_item_sk AS item_no, i_product_name AS title, i_current_price AS price "
            "FROM item",
            "Item table from the previous schema version. Replaced by item.",
        ),
        (
            "old_store_sales_2019",
            "SELECT * FROM store_sales WHERE ROWNUM <= 2000",
            "Archived slice of store_sales kept after the 2019 re-platforming.",
        ),
        (
            "legacy_orders",
            "SELECT cs_order_number AS order_id, cs_bill_customer_sk AS cust_id, "
            "cs_ext_sales_price AS amount FROM catalog_sales WHERE ROWNUM <= 2000",
            "Order table of the retired catalog system.",
        ),
        (
            "legacy_product",
            "SELECT i_item_id AS sku, i_brand AS brand, i_category AS category FROM item",
            "Product master of the retired catalog system.",
        ),
        (
            "old_address_book",
            "SELECT ca_address_sk AS id, ca_city AS city, ca_state AS state, ca_zip AS zip "
            "FROM customer_address",
            "Address list from the previous schema version.",
        ),
        (
            "legacy_web_orders",
            "SELECT ws_order_number AS order_id, ws_bill_customer_sk AS cust_id, "
            "ws_ext_sales_price AS amount FROM web_sales WHERE ROWNUM <= 2000",
            "Order table of the first web shop.",
        ),
        (
            "old_inventory_snapshot",
            "SELECT * FROM inventory WHERE ROWNUM <= 2000",
            "One-off inventory snapshot taken before a warehouse move.",
        ),
        (
            "old_promo_calendar",
            "SELECT p_promo_sk AS promo_id, p_promo_name AS name, p_start_date_sk AS start_sk "
            "FROM promotion",
            "Promotion calendar from the previous schema version.",
        ),
        (
            "legacy_returns",
            "SELECT sr_ticket_number AS ticket, sr_item_sk AS item_no, sr_return_amt AS amount "
            "FROM store_returns WHERE ROWNUM <= 2000",
            "Returns table of the retired point-of-sale system.",
        ),
    ]
    return [
        NoiseTable(name=name, kind="deprecated", status="dead", select_sql=sql, comment=comment)
        for name, sql, comment in specs
    ]


def _scratch() -> list[NoiseTable]:
    personal = [
        NoiseTable(
            name=f"tmp_{user}_{topic}",
            kind="scratch",
            status="dead",
            select_sql=sql,
            comment=f"Ad-hoc working table created by {user}.",
        )
        for user, (topic, sql) in product(_SCRATCH_USERS, _SCRATCH_TOPICS.items())
    ]
    throwaway = [
        NoiseTable(
            name=name,
            kind="scratch",
            status="dead",
            select_sql="SELECT LEVEL AS id FROM dual CONNECT BY LEVEL <= 10",
            comment="No description.",
        )
        for name in _THROWAWAY_NAMES
    ]
    return personal + throwaway


def _domain() -> list[NoiseTable]:
    # Tables from other business areas. They are not copies of anything; they
    # exist to make "find the right table" a real search problem.
    specs: list[tuple[str, Status, str, str]] = [
        (
            "employee",
            "active",
            "SELECT LEVEL AS employee_id, "
            "CASE WHEN LEVEL = 1 THEN NULL ELSE CEIL((LEVEL - 1) / 4) END AS manager_id, "
            "'Employee ' || LEVEL AS full_name, MOD(LEVEL, 8) + 1 AS department_id, "
            "DATE '2015-01-01' + LEVEL * 9 AS hire_date "
            "FROM dual CONNECT BY LEVEL <= 200",
            "Staff directory. manager_id points to employee_id of the direct manager.",
        ),
        (
            "department",
            "active",
            "SELECT LEVEL AS department_id, 'Department ' || LEVEL AS department_name "
            "FROM dual CONNECT BY LEVEL <= 8",
            "Departments referenced by employee.department_id.",
        ),
        (
            "marketing_campaign",
            "active",
            "SELECT p_promo_sk AS campaign_id, p_promo_name AS campaign_name, "
            "p_channel_email AS uses_email, p_channel_tv AS uses_tv, p_cost AS budget "
            "FROM promotion",
            "Marketing campaigns with channel flags and budget.",
        ),
        (
            "loyalty_member",
            "active",
            "SELECT c_customer_sk AS customer_sk, MOD(c_customer_sk, 4) + 1 AS tier, "
            "c_customer_sk * 37 AS points_balance FROM customer WHERE MOD(c_customer_sk, 3) = 0",
            "Loyalty programme membership: tier 1 (basic) to 4 (platinum) per customer.",
        ),
        (
            "supplier",
            "active",
            "SELECT DISTINCT i_manufact_id AS supplier_id, i_manufact AS supplier_name "
            "FROM item WHERE i_manufact_id IS NOT NULL",
            "Suppliers, keyed by the manufacturer id used on item.",
        ),
        (
            "price_history",
            "active",
            "SELECT i_item_sk AS item_sk, DATE '2002-01-01' AS valid_from, "
            "i_current_price AS price FROM item",
            "Price changes per item with the date each price took effect.",
        ),
        (
            "currency_rate",
            "active",
            "SELECT DATE '2002-01-01' + LEVEL AS rate_date, 'EUR' AS currency, "
            "1.05 + MOD(LEVEL, 10) / 100 AS usd_rate FROM dual CONNECT BY LEVEL <= 365",
            "Daily exchange rates to US dollars.",
        ),
        (
            "sales_target",
            "active",
            "SELECT s_store_sk AS store_sk, d_year AS target_year, d_qoy AS target_quarter, "
            "250000 AS target_amount FROM store CROSS JOIN "
            "(SELECT DISTINCT d_year, d_qoy FROM date_dim WHERE d_year BETWEEN 1998 AND 2003)",
            "Quarterly revenue targets per store.",
        ),
        (
            "holiday_calendar",
            "active",
            "SELECT d_date AS holiday_date, 'Holiday' AS holiday_name FROM date_dim "
            "WHERE d_holiday = 'Y' AND d_year BETWEEN 1998 AND 2003",
            "Public holidays used by scheduling and reporting.",
        ),
        (
            "etl_run_log",
            "active",
            "SELECT LEVEL AS run_id, DATE '2024-01-01' + LEVEL AS run_date, "
            "CASE WHEN MOD(LEVEL, 17) = 0 THEN 'FAILED' ELSE 'OK' END AS status "
            "FROM dual CONNECT BY LEVEL <= 300",
            "One row per nightly ETL run with its outcome.",
        ),
        (
            "product_review",
            "dead",
            "SELECT i_item_sk AS item_sk, MOD(i_item_sk, 5) + 1 AS stars FROM item",
            "Customer star ratings per item. Feed discontinued.",
        ),
        (
            "email_event",
            "dead",
            "SELECT c_customer_sk AS customer_sk, 'OPEN' AS event_type, "
            "DATE '2021-03-01' AS event_date FROM customer WHERE ROWNUM <= 500",
            "Email open and click events from a former marketing vendor.",
        ),
        (
            "store_audit",
            "dead",
            "SELECT s_store_sk AS store_sk, DATE '2020-06-30' AS audit_date, 'PASS' AS result "
            "FROM store",
            "Results of the 2020 store compliance audit.",
        ),
        (
            "clickstream_session",
            "dead",
            "SELECT LEVEL AS session_id, MOD(LEVEL, 1000) + 1 AS customer_sk, "
            "MOD(LEVEL, 40) + 1 AS page_views FROM dual CONNECT BY LEVEL <= 2000",
            "Web sessions from a clickstream pilot that was never rolled out.",
        ),
        (
            "gift_card",
            "dead",
            "SELECT LEVEL AS card_id, 50 AS initial_balance, MOD(LEVEL, 50) AS balance "
            "FROM dual CONNECT BY LEVEL <= 500",
            "Gift cards from a programme that ended.",
        ),
    ]
    return [
        NoiseTable(name=name, kind="domain", status=status, select_sql=sql, comment=comment)
        for name, status, sql, comment in specs
    ]


NOISE_TABLES: tuple[NoiseTable, ...] = (
    *_backups(),
    *_staging(),
    *_duplicates(),
    *_deprecated(),
    *_scratch(),
    *_domain(),
)

# --- Views ---------------------------------------------------------------------

NOISE_VIEWS: tuple[NoiseView, ...] = (
    NoiseView(
        name="v_daily_store_sales",
        status="active",
        select_sql=(
            "SELECT d.d_date AS sales_date, ss.ss_store_sk AS store_sk, "
            "SUM(ss.ss_net_paid) AS revenue, COUNT(*) AS line_count "
            "FROM store_sales ss JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk "
            "GROUP BY d.d_date, ss.ss_store_sk"
        ),
        comment="Store revenue per day. Source for the daily sales dashboard.",
    ),
    NoiseView(
        name="v_customer_360",
        status="active",
        select_sql=(
            "SELECT c.c_customer_sk, c.c_first_name, c.c_last_name, ca.ca_state, "
            "NVL(lm.tier, 0) AS loyalty_tier "
            "FROM customer c, customer_address ca, loyalty_member lm "
            "WHERE c.c_current_addr_sk = ca.ca_address_sk(+) "
            "AND c.c_customer_sk = lm.customer_sk(+)"
        ),
        comment="One row per customer with address and loyalty tier.",
    ),
    NoiseView(
        name="v_item_margin",
        status="active",
        select_sql=(
            "SELECT i_item_sk, i_category, i_current_price, i_wholesale_cost, "
            "margin_pct(i_current_price, i_wholesale_cost) AS margin_pct FROM item"
        ),
        comment="Margin percentage per item, from list price and wholesale cost.",
    ),
    NoiseView(
        name="v_old_sales_summary",
        status="dead",
        select_sql=(
            "SELECT ss_sold_date_sk, SUM(ss_net_paid) AS revenue "
            "FROM old_store_sales_2019 GROUP BY ss_sold_date_sk"
        ),
        comment="Summary over the archived 2019 sales slice.",
    ),
)

# --- Functions -----------------------------------------------------------------

NOISE_FUNCTIONS: tuple[NoiseFunction, ...] = (
    # net price: three teams, three spellings of "price minus discount"
    NoiseFunction(
        name="fn_net_price",
        status="active",
        group="net_price",
        signature="(p_price NUMBER, p_discount NUMBER) RETURN NUMBER",
        body="p_price - NVL(p_discount, 0)",
    ),
    NoiseFunction(
        name="calc_net_amount",
        status="active",
        group="net_price",
        signature="(amount NUMBER, disc NUMBER) RETURN NUMBER",
        body="amount - COALESCE(disc, 0)",
    ),
    NoiseFunction(
        name="get_discounted_price",
        status="dead",
        group="net_price",
        signature="(price NUMBER, discount NUMBER) RETURN NUMBER",
        body="CASE WHEN discount IS NULL THEN price ELSE price - discount END",
    ),
    # calendar quarter of a date
    NoiseFunction(
        name="fiscal_quarter",
        status="active",
        group="quarter_of_date",
        signature="(p_date DATE) RETURN NUMBER",
        body="TO_NUMBER(TO_CHAR(p_date, 'Q'))",
    ),
    NoiseFunction(
        name="get_qtr",
        status="active",
        group="quarter_of_date",
        signature="(d DATE) RETURN NUMBER",
        body="CEIL(EXTRACT(MONTH FROM d) / 3)",
    ),
    # "First Last" display name
    NoiseFunction(
        name="full_name",
        status="active",
        group="display_name",
        signature="(p_first VARCHAR2, p_last VARCHAR2) RETURN VARCHAR2",
        body="TRIM(p_first) || ' ' || TRIM(p_last)",
    ),
    NoiseFunction(
        name="fmt_customer_name",
        status="active",
        group="display_name",
        signature="(f VARCHAR2, l VARCHAR2) RETURN VARCHAR2",
        body="RTRIM(LTRIM(f)) || ' ' || RTRIM(LTRIM(l))",
    ),
    # margin as a percentage of price
    NoiseFunction(
        name="margin_pct",
        status="active",
        group="margin_percent",
        signature="(p_price NUMBER, p_cost NUMBER) RETURN NUMBER",
        body="CASE WHEN p_price = 0 THEN NULL "
        "ELSE ROUND((p_price - p_cost) / p_price * 100, 2) END",
    ),
    NoiseFunction(
        name="profit_margin",
        status="active",
        group="margin_percent",
        signature="(sales NUMBER, cost NUMBER) RETURN NUMBER",
        body="ROUND(100 * (sales - cost) / NULLIF(sales, 0), 2)",
    ),
    # one of a kind
    NoiseFunction(
        name="is_weekend",
        status="active",
        group=None,
        signature="(p_date DATE) RETURN NUMBER",
        body="CASE WHEN TO_CHAR(p_date, 'DY', 'NLS_DATE_LANGUAGE=ENGLISH') IN ('SAT', 'SUN') "
        "THEN 1 ELSE 0 END",
    ),
    NoiseFunction(
        name="age_band",
        status="active",
        group=None,
        signature="(p_birth_year NUMBER) RETURN VARCHAR2",
        body="CASE WHEN p_birth_year >= 1980 THEN 'UNDER_45' "
        "WHEN p_birth_year >= 1960 THEN '45_TO_64' ELSE '65_PLUS' END",
    ),
    NoiseFunction(
        name="legacy_tax_calc",
        status="dead",
        group=None,
        signature="(p_amount NUMBER) RETURN NUMBER",
        body="ROUND(p_amount * 0.0825, 2)",
    ),
)

# --- Answer key ----------------------------------------------------------------


def build_manifest() -> dict[str, object]:
    """The planted defects, as plain data ready to be written as JSON.

    Names are upper-case because that is how Oracle's catalog reports them.
    """
    groups: dict[str, list[str]] = {}
    for function in NOISE_FUNCTIONS:
        if function.group is not None:
            groups.setdefault(function.group, []).append(function.name.upper())

    return {
        "dead_tables": sorted(t.name.upper() for t in NOISE_TABLES if t.status == "dead"),
        "dead_views": sorted(v.name.upper() for v in NOISE_VIEWS if v.status == "dead"),
        "dead_functions": sorted(f.name.upper() for f in NOISE_FUNCTIONS if f.status == "dead"),
        "duplicate_tables": sorted(
            (
                {"table": t.name.upper(), "duplicate_of": t.duplicate_of.upper()}
                for t in NOISE_TABLES
                if t.duplicate_of is not None
            ),
            key=lambda entry: entry["table"],
        ),
        "duplicate_function_groups": {
            group: sorted(names) for group, names in sorted(groups.items())
        },
    }
