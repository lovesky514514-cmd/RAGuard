"""退避预算只影响允许重试的调用，不覆盖危险操作禁令。"""

import tempfile
import unittest
from pathlib import Path

from raguard.gateway import Gateway, RetryBudget
from raguard.identity import BusinessIntentRegistry
from raguard.ledger import Ledger
from tests.identity_helpers import trusted
from tests.test_gateway import FakeUpstream, request


class RetryBudgetTests(unittest.TestCase):
    def test_invalid_budget_rejected(self):
        for kwargs in ({'attempts': 0}, {'attempts': 11},
                       {'initial_delay_seconds': -1}, {'multiplier': 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RetryBudget(**kwargs)

    def test_opaque_write_never_retries_even_if_budget_is_ten(self):
        with tempfile.TemporaryDirectory() as temp:
            upstream = FakeUpstream(fail_after=True)
            gateway = Gateway(Ledger(str(Path(temp)/'ledger.db')), upstream,
                              identities=BusinessIntentRegistry(str(Path(temp)/'ids.db')),
                              retry_budget=RetryBudget(attempts=10, initial_delay_seconds=0))
            raw = request('notifications.send')
            self.assertEqual(gateway.call(raw, identity=trusted(raw))[0], 409)
            self.assertEqual(upstream.executions, 1)


if __name__ == '__main__':
    unittest.main()
