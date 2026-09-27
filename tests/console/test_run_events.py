import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from spark_console.db import create_schema
from spark_console.models import SparkTask, TaskRun, User
from spark_console.run_events import for_runs, record


class RunEventTests(unittest.TestCase):
    def test_events_are_ordered_and_persisted_per_run(self):
        engine = create_engine("sqlite:///:memory:")
        create_schema(engine)
        now = datetime(2026, 9, 27, tzinfo=timezone.utc)
        with Session(engine) as db:
            user = User(username="timeline-user", password_hash="hash")
            db.add(user)
            db.flush()
            task = SparkTask(
                owner_user_id=user.id,
                target_name="好友",
                send_time="09:00",
                message_template="火花",
            )
            db.add(task)
            db.flush()
            run = TaskRun(task_id=task.id, scheduled_for=now)
            db.add(run)
            db.flush()
            record(run, "claimed", "claimed", "Worker 已领取", now)
            record(run, "target_search", "selecting_target", "正在找好友", now)
            db.commit()
            timeline = for_runs(db, [run.id])[run.id]
            self.assertEqual([1, 2], [item.sequence for item in timeline])
            self.assertEqual("target_search", timeline[-1].event)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
