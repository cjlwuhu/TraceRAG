"""Browser fixtures must work from tracked source without historical outputs."""

import asyncio
from pathlib import Path
import tempfile
import unittest

from easyrag.console.store import Catalog, read_json
from serve_console_fixture import prepare_browser_fixture


class BrowserFixtureTests(unittest.TestCase):
    def test_fixture_builds_isolated_event_orders_and_synthetic_warning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asyncio.run(prepare_browser_fixture(root))
            events = Catalog(root).events()["items"]
            self.assertEqual(1, len(events))
            self.assertEqual(["fixture_observation_rank"], events[0]["methods"])
            catalog = Catalog(root)
            self.assertGreaterEqual(len(list(catalog.orders.glob("gen-*/work-order.json"))), 3)
            legacy = read_json(catalog.orders / "gen-29028755684a403ab86b9cb37958c07e/work-order.json")
            self.assertEqual("0.7", legacy["schema_version"])
            warning = read_json(catalog.orders / "gen-87c6bcfc46b44a338b2ba0f62a94aeed/engineering-review.json")
            self.assertTrue(warning["synthetic_fixture"])
            self.assertEqual("not_fully_supported", warning["semantic_support"])
            self.assertFalse(any(root.glob("**/*.private.csv")))
