import unittest
from datetime import datetime, timezone

from sqlalchemy.dialects import postgresql, sqlite

from spark_console.scheduler import (
    _apply_due_task_claim_lock,
    _due_task_statement,
)


class SchedulerSqlTests(unittest.TestCase):
    def test_postgresql_due_task_claim_uses_non_blocking_task_row_lock(self):
        statement = _apply_due_task_claim_lock(
            _due_task_statement(datetime(2026, 8, 25, 1, tzinfo=timezone.utc)),
            "postgresql",
        )
        sql = str(statement.compile(dialect=postgresql.dialect()))

        self.assertIn("FOR UPDATE OF spark_tasks SKIP LOCKED", sql)
        self.assertNotIn("FOR UPDATE OF users", sql)
        self.assertNotIn("FOR UPDATE OF douyin_accounts", sql)

    def test_sqlite_due_task_query_keeps_existing_lock_free_sql(self):
        statement = _apply_due_task_claim_lock(
            _due_task_statement(datetime(2026, 8, 25, 1, tzinfo=timezone.utc)),
            "sqlite",
        )
        sql = str(statement.compile(dialect=sqlite.dialect()))

        self.assertNotIn("FOR UPDATE", sql)
        self.assertIn("LIMIT", sql)

    def test_unknown_dialect_keeps_lock_free_fallback(self):
        statement = _apply_due_task_claim_lock(
            _due_task_statement(datetime(2026, 8, 25, 1, tzinfo=timezone.utc)),
            "mysql",
        )
        sql = str(statement.compile(dialect=sqlite.dialect()))

        self.assertNotIn("FOR UPDATE", sql)


if __name__ == "__main__":
    unittest.main()
