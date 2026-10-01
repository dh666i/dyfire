import unittest

from spark_console.worker_sharding import (
    MAX_SHARDS,
    account_dispatch_key,
    account_shard,
    owns_account,
    validate_shard,
)


class WorkerShardingTests(unittest.TestCase):
    def test_assignment_is_stable_and_covers_exactly_one_shard(self):
        account_id = "account-42"
        self.assertEqual(account_dispatch_key(account_id), account_dispatch_key(account_id))
        assigned = [index for index in range(8) if owns_account(account_id, index, 8)]
        self.assertEqual([account_shard(account_id, 8)], assigned)

    def test_shard_count_changes_are_explicitly_validated(self):
        self.assertEqual((2, 4), validate_shard("2", "4"))
        with self.assertRaises(ValueError):
            validate_shard(4, 4)
        with self.assertRaises(ValueError):
            validate_shard(0, 0)
        with self.assertRaises(ValueError):
            validate_shard(0, MAX_SHARDS + 1)


if __name__ == "__main__":
    unittest.main()
