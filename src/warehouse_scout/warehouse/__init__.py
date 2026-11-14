"""Read-only access to the source and target warehouses."""

from warehouse_scout.warehouse.readonly import Database, QueryResult, run_readonly

__all__ = ["Database", "QueryResult", "run_readonly"]
