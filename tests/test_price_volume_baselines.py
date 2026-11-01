import math
import statistics
import unittest

import numpy as np
import pandas as pd

from research.price_volume_baselines import IDS, calculate_baselines


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.calendar = pd.bdate_range("2020-01-01", periods=62).strftime("%Y-%m-%d").tolist()
        self.frame = pd.DataFrame({"date": self.calendar, "symbol": "000001.SZ",
                                   "close": np.arange(10., 72.), "open": np.arange(10., 72.) * 1.02,
                                   "high": np.arange(10., 72.) + 2, "low": np.arange(10., 72.) - 1,
                                   "reference_preclose": np.arange(10., 72.),
                                   "volume_shares": np.arange(1., 63.), "amount_CNY": 1000.,
                                   "turnover_fraction": np.arange(1., 63.) / 1000})

    def test_independent_formulas(self):
        result = calculate_baselines(self.frame, self.calendar, self.calendar[-1])
        self.assertEqual(set(result), set(IDS))
        self.assertTrue(all(item["price_kind"] == "raw_quote_diagnostic" for item in result.values()))
        self.assertAlmostEqual(result["momentum_5"]["value"], 71/66-1)
        self.assertAlmostEqual(result["momentum_20"]["value"], 71/51-1)
        self.assertAlmostEqual(result["momentum_60"]["value"], 71/11-1)
        self.assertAlmostEqual(result["reversal_5"]["value"], -(71/66-1))
        returns20 = [(price+1)/price-1 for price in range(51, 71)]
        returns60 = [(price+1)/price-1 for price in range(11, 71)]
        self.assertAlmostEqual(result["volatility_20"]["value"], statistics.stdev(returns20))
        self.assertAlmostEqual(result["volatility_60"]["value"], statistics.stdev(returns60))
        self.assertAlmostEqual(result["amihud_20"]["value"], statistics.mean(abs(r)/1000 for r in returns20))
        turnover = [i/1000 for i in range(43, 63)]
        self.assertAlmostEqual(result["turnover_mean_20"]["value"], statistics.mean(turnover))
        self.assertAlmostEqual(result["turnover_cv_20"]["value"], statistics.stdev(turnover)/statistics.mean(turnover))
        self.assertAlmostEqual(result["volume_ratio_5_20"]["value"], statistics.mean(range(58, 63))/statistics.mean(range(43, 63)))
        self.assertAlmostEqual(result["close_location_mean_20"]["value"], 1/3)
        self.assertAlmostEqual(result["overnight_gap_mean_20"]["value"], .02)

    def test_missing_session_no_compression(self):
        data = self.frame.drop(index=55)
        result = calculate_baselines(data, self.calendar, self.calendar[-1])
        self.assertIsNotNone(result["momentum_5"]["value"])
        self.assertEqual(result["momentum_20"]["reason"], "missing_calendar_session")
        self.assertEqual(result["turnover_mean_20"]["reason"], "missing_calendar_session")

    def test_dependency_missing_and_constants(self):
        data = self.frame.copy()
        data["close"] = 10.
        data["high"] = 10.
        data["low"] = 10.
        data["turnover_fraction"] = 0.
        data["amount_CNY"] = np.nan
        result = calculate_baselines(data, self.calendar, self.calendar[-1])
        self.assertEqual(result["momentum_60"]["value"], 0.)
        self.assertEqual(result["volatility_20"]["value"], 0.)
        self.assertEqual(result["turnover_mean_20"]["value"], 0.)
        self.assertEqual(result["turnover_cv_20"]["reason"], "zero_turnover_mean")
        self.assertEqual(result["close_location_mean_20"]["reason"], "zero_intraday_range")
        self.assertEqual(result["amihud_20"]["reason"], "nonfinite_required_value")

    def test_shuffle_future_and_negative_dependency(self):
        signal = self.calendar[-2]
        expected = calculate_baselines(self.frame.iloc[:-1], self.calendar, signal)
        altered = self.frame.copy()
        altered.loc[61, "close"] = -999
        self.assertEqual(calculate_baselines(altered.sample(frac=1, random_state=5), self.calendar, signal), expected)
        altered.loc[60, "reference_preclose"] = -1
        result = calculate_baselines(altered, self.calendar, signal)
        self.assertEqual(result["overnight_gap_mean_20"]["reason"], "negative_required_value")
        self.assertEqual(result["momentum_20"], expected["momentum_20"])

    def test_identity_calendar_and_oos_fail(self):
        duplicate = pd.concat([self.frame, self.frame.iloc[-1:]])
        for frame, calendar, signal in ((duplicate, self.calendar, self.calendar[-1]),
                                        (self.frame, self.calendar[::-1], self.calendar[-1]),
                                        (self.frame, self.calendar, "2023-01-01"),
                                        (self.frame, self.calendar+["2023-01-01"], self.calendar[-1])):
            with self.assertRaises(ValueError):
                calculate_baselines(frame, calendar, signal)
        extra = self.frame.iloc[-1:].copy()
        extra["date"] = "2023-01-02"
        with self.assertRaises(ValueError):
            calculate_baselines(pd.concat([self.frame, extra]), self.calendar, self.calendar[-1])


if __name__ == "__main__":
    unittest.main()
