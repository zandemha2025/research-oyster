import unittest

from reporting.rollup import _movement
from reporting.render import _movement as render_movement


class MovementTests(unittest.TestCase):
    def test_numeric_change_has_absolute_and_percent_values(self):
        row = {"value": 150, "prior_value": 100}
        _movement(row, coverage_ok=True, baseline=False)
        self.assertEqual(row["state"], "UP")
        self.assertEqual(row["absolute_change"], 50)
        self.assertEqual(row["delta"], 50)

    def test_new_dropped_and_insufficient_states(self):
        cases = [
            ({"value": 5, "prior_value": 0}, True, "NEW"),
            ({"value": 0, "prior_value": 5}, True, "DROPPED"),
            ({"value": 10, "prior_value": 5}, False, "INSUFFICIENT_DATA"),
        ]
        for row, covered, expected in cases:
            with self.subTest(expected=expected):
                _movement(row, coverage_ok=covered, baseline=False)
                self.assertEqual(row["state"], expected)
                self.assertEqual(render_movement(row), expected.replace("_", " "))

    def test_first_week_is_labeled_baseline(self):
        row = {"value": 25, "prior_value": None}
        _movement(row, coverage_ok=True, baseline=True)
        self.assertEqual(row["state"], "BASELINE")


if __name__ == "__main__":
    unittest.main()
