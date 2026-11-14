"""The workload of the source warehouse, described as query templates.

Each template is one "shape" of query that some person or job runs. The log
generator (`query_log.py`) renders templates many times with different
literals to produce a query history.

The SQL is deliberately Oracle-flavoured: `(+)` joins, CONNECT BY, DECODE,
ROWNUM, NVL, MINUS, hints, MERGE. `features` names the constructs each
template uses; those are the things a migration has to translate, and later
the ground truth for "which queries will break".

Two kinds of defect are planted on purpose:
    waste            a wasteful pattern (full scan, cartesian join, over-polling)
    duplicate_group  templates that answer the same question in different SQL

Placeholders such as {year} are filled from PARAMETER_CHOICES.
"""

from dataclasses import dataclass

PARAMETER_CHOICES: dict[str, tuple[object, ...]] = {
    "year": (1998, 1999, 2000, 2001, 2002, 2003),
    "moy": tuple(range(1, 13)),
    "state": ("TN", "CA", "TX", "GA", "OH"),
    "category": ("Books", "Electronics", "Home", "Jewelry", "Music", "Shoes", "Sports"),
    "country": ("UNITED STATES", "CANADA", "GERMANY", "JAPAN"),
    "birth_year": (1950, 1960, 1970, 1980),
    "dept": tuple(range(1, 9)),
    "n": (10, 20, 50, 100),
    "days": (1, 7, 30),
    "qty": (10, 50, 100),
}


@dataclass(frozen=True)
class QueryTemplate:
    id: str
    user: str
    runs_per_day: float
    typical_ms: int
    sql: str
    features: tuple[str, ...] = ()
    waste: str | None = None
    duplicate_group: str | None = None


# (fact table, the column that holds its date key)
_STAGED_FACTS = (
    ("store_sales", "ss_sold_date_sk"),
    ("web_sales", "ws_sold_date_sk"),
    ("catalog_sales", "cs_sold_date_sk"),
    ("store_returns", "sr_returned_date_sk"),
    ("web_returns", "wr_returned_date_sk"),
    ("catalog_returns", "cr_returned_date_sk"),
    ("inventory", "inv_date_sk"),
)


def _staging_loads() -> list[QueryTemplate]:
    return [
        QueryTemplate(
            id=f"etl_load_stg_{fact}",
            user="etl_svc",
            runs_per_day=1,
            typical_ms=4_000,
            sql=(
                f"INSERT /*+ APPEND */ INTO stg_{fact} SELECT * FROM {fact} "
                f"WHERE {date_column} = (SELECT MAX({date_column}) FROM {fact})"
            ),
            features=("insert_select", "hint"),
        )
        for fact, date_column in _STAGED_FACTS
    ]


_DASHBOARD = [
    QueryTemplate(
        id="daily_revenue_dashboard",
        user="bi_dashboard",
        runs_per_day=24,
        typical_ms=350,
        sql=(
            "SELECT sales_date, store_sk, revenue FROM v_daily_store_sales "
            "WHERE sales_date >= TRUNC(SYSDATE) - {days} ORDER BY sales_date"
        ),
        features=("sysdate", "trunc_date", "date_arithmetic"),
    ),
    QueryTemplate(
        id="revenue_by_category",
        user="bi_dashboard",
        runs_per_day=24,
        typical_ms=900,
        sql=(
            "SELECT i.i_category, SUM(ss.ss_ext_sales_price) AS revenue "
            "FROM store_sales ss, item i, date_dim d "
            "WHERE ss.ss_item_sk = i.i_item_sk AND ss.ss_sold_date_sk = d.d_date_sk "
            "AND d.d_year = {year} AND d.d_moy = {moy} "
            "GROUP BY i.i_category ORDER BY revenue DESC"
        ),
        features=("comma_join",),
        duplicate_group="category_revenue",
    ),
    QueryTemplate(
        id="revenue_by_category_ansi",
        user="mlee",
        runs_per_day=3,
        typical_ms=950,
        sql=(
            "SELECT it.i_category AS category, SUM(s.ss_ext_sales_price) AS total_sales "
            "FROM store_sales s "
            "JOIN dim_item it ON it.i_item_sk = s.ss_item_sk "
            "JOIN dim_date dt ON dt.d_date_sk = s.ss_sold_date_sk "
            "WHERE dt.d_year = {year} AND dt.d_moy = {moy} "
            "GROUP BY it.i_category ORDER BY 2 DESC"
        ),
        duplicate_group="category_revenue",
    ),
    QueryTemplate(
        id="top_items",
        user="bi_dashboard",
        runs_per_day=24,
        typical_ms=1_200,
        sql=(
            "SELECT * FROM (SELECT i.i_product_name, SUM(ss.ss_net_paid) AS revenue "
            "FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk "
            "GROUP BY i.i_product_name ORDER BY revenue DESC) WHERE ROWNUM <= {n}"
        ),
        features=("rownum",),
    ),
    QueryTemplate(
        id="store_ranking",
        user="bi_dashboard",
        runs_per_day=12,
        typical_ms=800,
        sql=(
            "SELECT s_store_name, revenue, RANK() OVER (ORDER BY revenue DESC) AS rnk "
            "FROM (SELECT s.s_store_name, SUM(ss.ss_net_paid) AS revenue "
            "FROM store_sales ss JOIN dim_store s ON ss.ss_store_sk = s.s_store_sk "
            "GROUP BY s.s_store_name)"
        ),
        features=("analytic_function",),
    ),
    QueryTemplate(
        id="customer_segments",
        user="bi_dashboard",
        runs_per_day=6,
        typical_ms=400,
        sql=(
            "SELECT ca_state, loyalty_tier, COUNT(*) AS customers "
            "FROM v_customer_360 GROUP BY ca_state, loyalty_tier"
        ),
    ),
    QueryTemplate(
        id="hourly_traffic",
        user="bi_dashboard",
        runs_per_day=6,
        typical_ms=700,
        sql=(
            "SELECT t.t_hour, COUNT(*) AS sales FROM store_sales ss "
            "JOIN time_dim t ON ss.ss_sold_time_sk = t.t_time_sk "
            "GROUP BY t.t_hour ORDER BY t.t_hour"
        ),
    ),
]

_FINANCE = [
    QueryTemplate(
        id="quarterly_net_revenue_store",
        user="finance_app",
        runs_per_day=4,
        typical_ms=1_500,
        sql=(
            "SELECT fiscal_quarter(d.d_date) AS quarter, "
            "SUM(fn_net_price(ss.ss_list_price, ss.ss_ext_discount_amt)) AS net "
            "FROM store_sales ss JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk "
            "WHERE d.d_year = {year} GROUP BY fiscal_quarter(d.d_date)"
        ),
        features=("user_function",),
    ),
    QueryTemplate(
        id="quarterly_net_revenue_web",
        user="kwong",
        runs_per_day=2,
        typical_ms=1_100,
        sql=(
            "SELECT get_qtr(d.d_date) AS qtr, "
            "SUM(calc_net_amount(ws.ws_list_price, ws.ws_ext_discount_amt)) AS net_amount "
            "FROM web_sales ws JOIN dim_date d ON ws.ws_sold_date_sk = d.d_date_sk "
            "WHERE d.d_year = {year} GROUP BY get_qtr(d.d_date)"
        ),
        features=("user_function",),
    ),
    QueryTemplate(
        id="target_vs_actual",
        user="finance_app",
        runs_per_day=4,
        typical_ms=1_800,
        sql=(
            "SELECT t.store_sk, t.target_quarter, t.target_amount, NVL(a.revenue, 0) AS actual "
            "FROM sales_target t, "
            "(SELECT ss.ss_store_sk, d.d_year, d.d_qoy, SUM(ss.ss_net_paid) AS revenue "
            "FROM store_sales ss, date_dim d WHERE ss.ss_sold_date_sk = d.d_date_sk "
            "GROUP BY ss.ss_store_sk, d.d_year, d.d_qoy) a "
            "WHERE t.store_sk = a.ss_store_sk(+) AND t.target_year = a.d_year(+) "
            "AND t.target_quarter = a.d_qoy(+) AND t.target_year = {year}"
        ),
        features=("outer_join_plus", "nvl", "comma_join"),
    ),
    QueryTemplate(
        id="item_margin_by_category",
        user="finance_app",
        runs_per_day=2,
        typical_ms=300,
        sql=(
            "SELECT i_category, AVG(margin_pct) AS avg_margin FROM v_item_margin "
            "GROUP BY i_category"
        ),
    ),
    QueryTemplate(
        id="best_margin_items",
        user="apatel",
        runs_per_day=1,
        typical_ms=250,
        sql=(
            "SELECT i_item_id, profit_margin(i_current_price, i_wholesale_cost) AS margin "
            "FROM dim_item WHERE i_category = '{category}' "
            "ORDER BY margin DESC FETCH FIRST {n} ROWS ONLY"
        ),
        features=("user_function", "fetch_first"),
    ),
    QueryTemplate(
        id="revenue_in_eur",
        user="finance_app",
        runs_per_day=2,
        typical_ms=1_300,
        sql=(
            "SELECT d.d_date, SUM(ws.ws_ext_sales_price) / MAX(cr.usd_rate) AS revenue_eur "
            "FROM web_sales ws JOIN date_dim d ON ws.ws_sold_date_sk = d.d_date_sk "
            "JOIN currency_rate cr ON cr.rate_date = d.d_date AND cr.currency = 'EUR' "
            "GROUP BY d.d_date"
        ),
    ),
    QueryTemplate(
        id="quarter_pivot",
        user="finance_app",
        runs_per_day=1,
        typical_ms=2_200,
        sql=(
            "SELECT * FROM (SELECT d.d_qoy, i.i_category, ss.ss_net_paid FROM store_sales ss "
            "JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk "
            "JOIN item i ON ss.ss_item_sk = i.i_item_sk WHERE d.d_year = {year}) "
            "PIVOT (SUM(ss_net_paid) FOR d_qoy IN (1 AS q1, 2 AS q2, 3 AS q3, 4 AS q4))"
        ),
        features=("pivot",),
    ),
    QueryTemplate(
        id="monthly_orders",
        user="finance_app",
        runs_per_day=2,
        typical_ms=900,
        sql=(
            "SELECT TO_CHAR(d.d_date, 'YYYY-MM') AS month, COUNT(*) AS orders "
            "FROM catalog_sales cs JOIN date_dim d ON cs.cs_sold_date_sk = d.d_date_sk "
            "WHERE d.d_date BETWEEN TO_DATE('{year}-01-01', 'YYYY-MM-DD') "
            "AND TO_DATE('{year}-12-31', 'YYYY-MM-DD') "
            "GROUP BY TO_CHAR(d.d_date, 'YYYY-MM')"
        ),
        features=("to_char", "to_date"),
    ),
    QueryTemplate(
        id="running_profit",
        user="finance_app",
        runs_per_day=1,
        typical_ms=1_400,
        sql=(
            "SELECT d.d_date, SUM(SUM(ws.ws_net_profit)) OVER "
            "(ORDER BY d.d_date ROWS UNBOUNDED PRECEDING) AS running_profit "
            "FROM web_sales ws JOIN date_dim d ON ws.ws_sold_date_sk = d.d_date_sk "
            "WHERE d.d_year = {year} GROUP BY d.d_date"
        ),
        features=("analytic_function",),
    ),
    QueryTemplate(
        id="month_series",
        user="finance_app",
        runs_per_day=2,
        typical_ms=20,
        sql=(
            "SELECT ADD_MONTHS(DATE '{year}-01-01', LEVEL - 1) AS month_start "
            "FROM dual CONNECT BY LEVEL <= 12"
        ),
        features=("connect_by_level", "dual", "add_months"),
    ),
]

_ANALYSTS = [
    QueryTemplate(
        id="customers_missing_address",
        user="jsmith",
        runs_per_day=2,
        typical_ms=300,
        sql=(
            "SELECT c.c_customer_id, NVL(ca.ca_state, 'UNKNOWN') AS state "
            "FROM customer c, customer_address ca "
            "WHERE c.c_current_addr_sk = ca.ca_address_sk(+) AND c.c_birth_year > {birth_year}"
        ),
        features=("outer_join_plus", "nvl", "comma_join"),
    ),
    QueryTemplate(
        id="sales_by_channel",
        user="jsmith",
        runs_per_day=1,
        typical_ms=1_000,
        sql=(
            "SELECT DECODE(p.p_channel_email, 'Y', 'EMAIL', 'N', 'OTHER', 'UNKNOWN') AS channel, "
            "COUNT(*) AS sales FROM store_sales ss, promotion p "
            "WHERE ss.ss_promo_sk = p.p_promo_sk "
            "GROUP BY DECODE(p.p_channel_email, 'Y', 'EMAIL', 'N', 'OTHER', 'UNKNOWN')"
        ),
        features=("decode", "comma_join"),
    ),
    QueryTemplate(
        id="org_chart",
        user="mlee",
        runs_per_day=1,
        typical_ms=60,
        sql=(
            "SELECT LEVEL AS depth, LPAD(' ', 2 * (LEVEL - 1)) || full_name AS employee, "
            "SYS_CONNECT_BY_PATH(employee_id, '/') AS path FROM employee "
            "START WITH manager_id IS NULL CONNECT BY PRIOR employee_id = manager_id "
            "ORDER SIBLINGS BY full_name"
        ),
        features=("connect_by", "sys_connect_by_path", "concat_pipes"),
    ),
    QueryTemplate(
        id="tenure_by_department",
        user="mlee",
        runs_per_day=0.5,
        typical_ms=40,
        sql=(
            "SELECT e.employee_id, TRUNC(MONTHS_BETWEEN(SYSDATE, e.hire_date) / 12) AS years "
            "FROM employee e JOIN department dp ON e.department_id = dp.department_id "
            "WHERE dp.department_name = 'Department {dept}'"
        ),
        features=("months_between", "sysdate"),
    ),
    QueryTemplate(
        id="names_by_state",
        user="apatel",
        runs_per_day=1,
        typical_ms=200,
        sql=(
            "SELECT ca.ca_state, LISTAGG(c.c_last_name, ', ') WITHIN GROUP "
            "(ORDER BY c.c_last_name) AS names "
            "FROM customer c JOIN customer_address ca ON c.c_current_addr_sk = ca.ca_address_sk "
            "WHERE ca.ca_state = '{state}' AND ROWNUM <= 50 GROUP BY ca.ca_state"
        ),
        features=("listagg", "rownum"),
    ),
    QueryTemplate(
        id="web_only_customers",
        user="apatel",
        runs_per_day=0.5,
        typical_ms=1_600,
        sql=(
            "SELECT ws_bill_customer_sk FROM web_sales MINUS SELECT ss_customer_sk FROM store_sales"
        ),
        features=("minus",),
    ),
    QueryTemplate(
        id="return_rate_by_item",
        user="kwong",
        runs_per_day=1,
        typical_ms=2_500,
        sql=(
            "SELECT ss.ss_item_sk, COUNT(*) AS sold, "
            "SUM(NVL2(sr.sr_ticket_number, 1, 0)) AS returned "
            "FROM store_sales ss, store_returns sr "
            "WHERE ss.ss_ticket_number = sr.sr_ticket_number(+) "
            "AND ss.ss_item_sk = sr.sr_item_sk(+) GROUP BY ss.ss_item_sk"
        ),
        features=("outer_join_plus", "nvl2", "comma_join"),
    ),
    QueryTemplate(
        id="preferred_customer_names",
        user="jsmith",
        runs_per_day=1,
        typical_ms=150,
        sql=(
            "SELECT full_name(c_first_name, c_last_name) AS name, age_band(c_birth_year) AS band "
            "FROM customer WHERE c_preferred_cust_flag = 'Y'"
        ),
        features=("user_function",),
    ),
    QueryTemplate(
        id="customers_by_country",
        user="kwong",
        runs_per_day=1,
        typical_ms=140,
        sql=(
            "SELECT fmt_customer_name(c_first_name, c_last_name) AS customer_name, "
            "c_email_address FROM dim_customer WHERE c_birth_country = '{country}'"
        ),
        features=("user_function",),
    ),
    QueryTemplate(
        id="weekend_catalog_sales",
        user="apatel",
        runs_per_day=0.5,
        typical_ms=1_900,
        sql=(
            "SELECT is_weekend(d.d_date) AS weekend, SUM(cs.cs_ext_sales_price) AS sales "
            "FROM catalog_sales cs JOIN date_dim d ON cs.cs_sold_date_sk = d.d_date_sk "
            "GROUP BY is_weekend(d.d_date)"
        ),
        features=("user_function",),
    ),
    QueryTemplate(
        id="best_item_per_store",
        user="mlee",
        runs_per_day=0.5,
        typical_ms=1_700,
        sql=(
            "SELECT ss_store_sk, MAX(ss_item_sk) KEEP "
            "(DENSE_RANK FIRST ORDER BY ss_net_paid DESC) AS best_item "
            "FROM store_sales GROUP BY ss_store_sk"
        ),
        features=("keep_dense_rank",),
    ),
    QueryTemplate(
        id="email_pattern_check",
        user="jsmith",
        runs_per_day=0.3,
        typical_ms=180,
        sql=(
            "SELECT c_customer_id, c_email_address FROM customer "
            "WHERE REGEXP_LIKE(c_email_address, '^[a-z]+\\.[a-z]+@', 'i') AND ROWNUM <= {n}"
        ),
        features=("regexp_like", "rownum"),
    ),
    QueryTemplate(
        id="email_domains",
        user="kwong",
        runs_per_day=0.3,
        typical_ms=220,
        sql=(
            "SELECT SUBSTR(c_email_address, INSTR(c_email_address, '@') + 1) AS domain, "
            "COUNT(*) AS customers FROM customer "
            "GROUP BY SUBSTR(c_email_address, INSTR(c_email_address, '@') + 1)"
        ),
        features=("substr_instr",),
    ),
    QueryTemplate(
        id="low_inventory",
        user="apatel",
        runs_per_day=2,
        typical_ms=2_000,
        sql=(
            "SELECT /*+ PARALLEL(inv, 4) */ w.w_warehouse_name, i.i_item_id, "
            "inv.inv_quantity_on_hand FROM inventory inv, warehouse w, item i "
            "WHERE inv.inv_warehouse_sk = w.w_warehouse_sk AND inv.inv_item_sk = i.i_item_sk "
            "AND inv.inv_quantity_on_hand < {qty}"
        ),
        features=("hint", "comma_join"),
    ),
    QueryTemplate(
        id="profit_rollup",
        user="mlee",
        runs_per_day=1,
        typical_ms=1_500,
        sql=(
            "SELECT i.i_category, i.i_class, SUM(cs.cs_net_profit) AS profit "
            "FROM catalog_sales cs JOIN item i ON cs.cs_item_sk = i.i_item_sk "
            "GROUP BY ROLLUP (i.i_category, i.i_class)"
        ),
        features=("rollup",),
    ),
    QueryTemplate(
        id="spend_by_loyalty_tier",
        user="kwong",
        runs_per_day=1,
        typical_ms=1_300,
        sql=(
            "WITH spend AS (SELECT ss_customer_sk, SUM(ss_net_paid) AS total "
            "FROM store_sales GROUP BY ss_customer_sk) "
            "SELECT lm.tier, AVG(s.total) AS avg_spend FROM loyalty_member lm "
            "JOIN spend s ON lm.customer_sk = s.ss_customer_sk GROUP BY lm.tier"
        ),
    ),
    QueryTemplate(
        id="holiday_revenue",
        user="jsmith",
        runs_per_day=0.5,
        typical_ms=1_000,
        sql=(
            "SELECT h.holiday_date, SUM(ss.ss_net_paid) AS revenue FROM holiday_calendar h "
            "JOIN date_dim d ON d.d_date = h.holiday_date "
            "JOIN store_sales ss ON ss.ss_sold_date_sk = d.d_date_sk GROUP BY h.holiday_date"
        ),
    ),
    QueryTemplate(
        id="campaign_profit",
        user="apatel",
        runs_per_day=1,
        typical_ms=1_100,
        sql=(
            "SELECT mc.campaign_name, mc.budget, SUM(ss.ss_net_profit) AS profit "
            "FROM marketing_campaign mc JOIN store_sales ss ON ss.ss_promo_sk = mc.campaign_id "
            "WHERE mc.uses_email = 'Y' GROUP BY mc.campaign_name, mc.budget"
        ),
    ),
    QueryTemplate(
        id="supplier_price_floor",
        user="mlee",
        runs_per_day=0.5,
        typical_ms=300,
        sql=(
            "SELECT s.supplier_name, COUNT(*) AS items, MIN(ph.price) AS min_price "
            "FROM supplier s JOIN item i ON i.i_manufact_id = s.supplier_id "
            "JOIN price_history ph ON ph.item_sk = i.i_item_sk GROUP BY s.supplier_name"
        ),
    ),
    QueryTemplate(
        id="income_segments",
        user="kwong",
        runs_per_day=0.5,
        typical_ms=350,
        sql=(
            "SELECT ib.ib_lower_bound, ib.ib_upper_bound, COUNT(*) AS customers "
            "FROM customer c, household_demographics hd, income_band ib "
            "WHERE c.c_current_hdemo_sk = hd.hd_demo_sk "
            "AND hd.hd_income_band_sk = ib.ib_income_band_sk "
            "GROUP BY ib.ib_lower_bound, ib.ib_upper_bound"
        ),
        features=("comma_join",),
    ),
    QueryTemplate(
        id="demographics_breakdown",
        user="jsmith",
        runs_per_day=0.5,
        typical_ms=400,
        sql=(
            "SELECT DECODE(cd.cd_gender, 'M', 'Male', 'F', 'Female') AS gender, "
            "cd.cd_education_status, COUNT(*) AS customers FROM customer c "
            "JOIN customer_demographics cd ON c.c_current_cdemo_sk = cd.cd_demo_sk "
            "GROUP BY cd.cd_gender, cd.cd_education_status"
        ),
        features=("decode",),
    ),
    QueryTemplate(
        id="call_center_sales",
        user="apatel",
        runs_per_day=0.5,
        typical_ms=1_200,
        sql=(
            "SELECT cc.cc_name, cp.cp_catalog_page_number, SUM(cs.cs_ext_sales_price) AS sales "
            "FROM catalog_sales cs, call_center cc, catalog_page cp "
            "WHERE cs.cs_call_center_sk = cc.cc_call_center_sk "
            "AND cs.cs_catalog_page_sk = cp.cp_catalog_page_sk "
            "GROUP BY cc.cc_name, cp.cp_catalog_page_number"
        ),
        features=("comma_join",),
    ),
    QueryTemplate(
        id="web_orders_by_shipping",
        user="mlee",
        runs_per_day=0.5,
        typical_ms=1_000,
        sql=(
            "SELECT web.web_name, sm.sm_type, wp.wp_type, COUNT(*) AS orders "
            "FROM web_sales ws, web_site web, ship_mode sm, web_page wp "
            "WHERE ws.ws_web_site_sk = web.web_site_sk "
            "AND ws.ws_ship_mode_sk = sm.sm_ship_mode_sk "
            "AND ws.ws_web_page_sk = wp.wp_web_page_sk "
            "GROUP BY web.web_name, sm.sm_type, wp.wp_type"
        ),
        features=("comma_join",),
    ),
    QueryTemplate(
        id="return_reasons",
        user="kwong",
        runs_per_day=0.5,
        typical_ms=600,
        sql=(
            "SELECT r.r_reason_desc, COUNT(*) AS returns, SUM(wr.wr_return_amt) AS amount "
            "FROM web_returns wr LEFT JOIN reason r ON wr.wr_reason_sk = r.r_reason_sk "
            "GROUP BY r.r_reason_desc UNION ALL "
            "SELECT r.r_reason_desc, COUNT(*), SUM(cr.cr_return_amount) "
            "FROM catalog_returns cr LEFT JOIN reason r ON cr.cr_reason_sk = r.r_reason_sk "
            "GROUP BY r.r_reason_desc"
        ),
    ),
]

_ETL = [
    *_staging_loads(),
    QueryTemplate(
        id="etl_merge_dim_customer",
        user="etl_svc",
        runs_per_day=0.15,
        typical_ms=6_000,
        sql=(
            "MERGE INTO dim_customer t USING customer s ON (t.c_customer_sk = s.c_customer_sk) "
            "WHEN MATCHED THEN UPDATE SET t.c_email_address = s.c_email_address, "
            "t.c_current_addr_sk = s.c_current_addr_sk "
            "WHEN NOT MATCHED THEN INSERT (c_customer_sk, c_customer_id, c_first_name, "
            "c_last_name) VALUES (s.c_customer_sk, s.c_customer_id, s.c_first_name, "
            "s.c_last_name)"
        ),
        features=("merge",),
    ),
    QueryTemplate(
        id="etl_merge_dim_item",
        user="etl_svc",
        runs_per_day=0.15,
        typical_ms=3_000,
        sql=(
            "MERGE INTO dim_item t USING item s ON (t.i_item_sk = s.i_item_sk) "
            "WHEN MATCHED THEN UPDATE SET t.i_current_price = s.i_current_price "
            "WHEN NOT MATCHED THEN INSERT (i_item_sk, i_item_id, i_current_price) "
            "VALUES (s.i_item_sk, s.i_item_id, s.i_current_price)"
        ),
        features=("merge",),
    ),
    QueryTemplate(
        id="etl_refresh_dim_date",
        user="etl_svc",
        runs_per_day=0.15,
        typical_ms=2_500,
        sql=(
            "INSERT /*+ APPEND */ INTO dim_date SELECT * FROM date_dim "
            "WHERE d_date_sk NOT IN (SELECT d_date_sk FROM dim_date)"
        ),
        features=("insert_select", "hint"),
    ),
    QueryTemplate(
        id="etl_refresh_dim_store",
        user="etl_svc",
        runs_per_day=0.15,
        typical_ms=500,
        sql=(
            "UPDATE dim_store t SET t.s_manager = "
            "(SELECT s.s_manager FROM store s WHERE s.s_store_sk = t.s_store_sk)"
        ),
        features=("correlated_update",),
    ),
    QueryTemplate(
        id="etl_failures",
        user="monitoring",
        runs_per_day=24,
        typical_ms=15,
        sql=(
            "SELECT run_id, run_date FROM etl_run_log "
            "WHERE status = 'FAILED' AND run_date > SYSDATE - {days}"
        ),
        features=("sysdate", "date_arithmetic"),
    ),
]

_WASTEFUL = [
    QueryTemplate(
        id="waste_full_scan_store_sales",
        user="jsmith",
        runs_per_day=2,
        typical_ms=45_000,
        sql="SELECT * FROM store_sales",
        waste="select_star_without_filter",
    ),
    QueryTemplate(
        id="waste_cartesian_join",
        user="apatel",
        runs_per_day=0.3,
        typical_ms=180_000,
        sql=(
            "SELECT c.c_customer_id, cd.cd_credit_rating "
            "FROM customer c, customer_demographics cd WHERE c.c_birth_year = {birth_year}"
        ),
        features=("comma_join",),
        waste="cartesian_join",
    ),
    QueryTemplate(
        id="waste_polling_total",
        user="monitoring",
        runs_per_day=288,
        typical_ms=30_000,
        sql="SELECT COUNT(*), SUM(ss_net_paid) FROM store_sales",
        waste="excessive_frequency",
    ),
]

QUERY_TEMPLATES: tuple[QueryTemplate, ...] = (
    *_DASHBOARD,
    *_FINANCE,
    *_ANALYSTS,
    *_ETL,
    *_WASTEFUL,
)
