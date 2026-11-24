"""Sort and filter choices shared by Home's library grid and a collection's page."""

import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

with patch.dict(sys.modules, {"xbmc": types.SimpleNamespace(log=MagicMock(), LOGERROR=4),
                              "xbmcgui": types.SimpleNamespace(ListItem=MagicMock)}):
    controls = importlib.import_module("resources.lib.silo.catalog_controls")


class SortTests(unittest.TestCase):
    def test_a_leading_minus_sorts_descending_and_reversing_flips_it(self):
        self.assertEqual(controls.sort_term("title", False), "title")
        self.assertEqual(controls.sort_term("title", True), "-title")
        self.assertEqual(controls.sort_term("-added_at", True), "added_at")

    def test_collection_order_sends_no_sort_and_has_no_direction(self):
        self.assertEqual(controls.sort_term("", True), "")
        self.assertEqual(controls.COLLECTION_SORTS[0][0], "")
        self.assertEqual(controls.sort_chip(controls.COLLECTION_SORTS, "", False), "Collection order")
        self.assertEqual(controls.sort_chip(controls.COLLECTION_SORTS, "title", True), "Title (reversed)")
        dialog = MagicMock()
        dialog.select.return_value = -1
        controls.choose_sort(dialog, controls.COLLECTION_SORTS, "", False)
        labels = dialog.select.call_args.args[1]
        self.assertEqual(labels[0], "Collection order")
        self.assertFalse(any(label.startswith("Reverse order") for label in labels))

    def test_choose_sort_returns_the_new_choice_or_none(self):
        dialog = MagicMock()
        values = [value for value, _label, _chip in controls.SORTS]
        dialog.select.return_value = values.index("-year")
        self.assertEqual(controls.choose_sort(dialog, controls.SORTS, "title", True), ("-year", False))
        dialog.select.return_value = len(values)  # Reverse order
        self.assertEqual(controls.choose_sort(dialog, controls.SORTS, "title", False), ("title", True))
        dialog.select.return_value = values.index("title")
        self.assertIsNone(controls.choose_sort(dialog, controls.SORTS, "title", False))
        dialog.select.return_value = -1
        self.assertIsNone(controls.choose_sort(dialog, controls.SORTS, "title", False))


class FilterTests(unittest.TestCase):
    def test_filters_lower_to_rule_groups(self):
        groups = controls.filter_groups({"genre": ["Drama", "Crime"], "watch_status": ["unwatched"]})
        self.assertEqual(groups, [
            {"match": "any", "rules": [{"field": "genre", "op": "contains", "value": "Crime"},
                                       {"field": "genre", "op": "contains", "value": "Drama"}]},
            {"match": "all", "rules": [{"field": "watched", "op": "is", "value": False}]}])
        self.assertEqual(controls.active_facets({"genre": ["Drama"], "decade": [], "studio": ["X"]}), 2)

    def test_choose_filters_edits_the_selection_and_says_whether_it_changed(self):
        dialog = MagicMock()
        dialog.select.side_effect = [0, -1]  # Genre, then close.
        dialog.multiselect.return_value = [1]
        filters = {}
        changed = controls.choose_filters(dialog, filters, {"genres": ["Crime", "Drama"]}, search=None)
        self.assertTrue(changed)
        self.assertEqual(filters, {"genre": ["Drama"]})
        dialog.select.side_effect = [-1]
        self.assertFalse(controls.choose_filters(dialog, filters, {"genres": ["Crime"]}, search=None))


if __name__ == "__main__":
    unittest.main()
