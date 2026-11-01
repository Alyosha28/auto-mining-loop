import math
import statistics
import unittest

import numpy as np
import pandas as pd

from research.price_volume_baselines import calculate_baselines
from research.price_volume_batch import compute_signal_panel


class BatchTests(unittest.TestCase):
    def setUp(self):
        # 420 sessions so the 250-session (dist_52w_high_250) and 80-session
        # (volume_shock_20) windows have genuinely valid paths, not just missing ones.
        count = 420
        self.calendar = pd.bdate_range("2020-01-01", periods=count).strftime("%Y-%m-%d").tolist()
        steps = np.arange(count)
        price = 10*np.exp(np.cumsum(.01*np.sin(steps)+.0005))
        volume = np.arange(1., count+1.)
        volume[405] = 1e7
        volume[410] = 1e7
        self.frame = pd.DataFrame({"date": self.calendar, "symbol": "000001.SZ",
                                   "close": price, "open": price*1.005,
                                   "high": price*1.02, "low": price*.98,
                                   "reference_preclose": np.r_[price[0], price[:-1]],
                                   "volume_shares": volume, "amount_CNY": volume*1000.,
                                   "turnover_fraction": np.arange(1., count+1.)/1000})
        self.signals = [self.calendar[i] for i in (3, 20, 30, 60, 80, 120, 150, 200, 249, 250, 300, 360, 419)]

    def assert_reference(self, frame, signals=None):
        signals = self.signals if signals is None else signals
        panel = compute_signal_panel(frame, self.calendar, signals).set_index(["date", "candidate_id"])
        for date in signals:
            scalar = calculate_baselines(frame, self.calendar, date)
            for candidate, record in scalar.items():
                actual = panel.loc[(date, candidate)]
                if record["value"] is None:
                    self.assertTrue(pd.isna(actual.value), (date, candidate))
                    self.assertIsNotNone(actual.reason)
                else:
                    self.assertAlmostEqual(actual.value, record["value"], places=11, msg=str((date, candidate)))
                    self.assertTrue(pd.isna(actual.reason))

    def test_valid_shuffle_and_missing(self):
        self.assert_reference(self.frame)
        self.assert_reference(self.frame.sample(frac=1, random_state=11))
        self.assert_reference(self.frame.drop(index=[15, 65]))
        self.assert_reference(self.frame.drop(index=[15, 65, 260]))

    def test_invalid_numeric_and_constants(self):
        invalid = self.frame.copy()
        invalid.loc[19, "close"] = np.nan
        invalid.loc[75, "amount_CNY"] = np.inf
        invalid.loc[68, "reference_preclose"] = -1
        invalid.loc[84, "high"] = invalid.loc[84, "low"]
        invalid.loc[285, "close"] = np.nan
        self.assert_reference(invalid)
        constant = self.frame.copy()
        constant["close"] = 10.
        constant["volume_shares"] = 0.
        constant["turnover_fraction"] = 0.
        constant["high"] = 10.
        constant["low"] = 10.
        constant["amount_CNY"] = 0.
        self.assert_reference(constant)

    def test_future_values_and_unused_columns(self):
        original = compute_signal_panel(self.frame, self.calendar, [self.calendar[60]])
        altered = self.frame.copy()
        altered.loc[61:, "close"] = -999
        altered["unused"] = np.nan
        pd.testing.assert_frame_equal(original, compute_signal_panel(altered, self.calendar, [self.calendar[60]]))
        deep = self.frame.copy()
        deep.loc[301:, "close"] = -999
        deep.loc[301:, "high"] = -999
        deep.loc[301:, "volume_shares"] = -999
        deep["unused"] = np.nan
        pd.testing.assert_frame_equal(compute_signal_panel(self.frame, self.calendar, [self.calendar[300]]),
                                      compute_signal_panel(deep, self.calendar, [self.calendar[300]]))

    def test_long_window_boundaries(self):
        insufficient = calculate_baselines(self.frame, self.calendar, self.calendar[248])
        self.assertIsNone(insufficient["dist_52w_high_250"]["value"])
        self.assertEqual(insufficient["dist_52w_high_250"]["reason"], "insufficient_calendar_history")
        exact = calculate_baselines(self.frame, self.calendar, self.calendar[249])
        self.assertIsNotNone(exact["dist_52w_high_250"]["value"])
        self.assertIsNone(exact["dist_52w_high_250"]["reason"])
        self.assertAlmostEqual(exact["dist_52w_high_250"]["value"],
                               self.frame.close.iloc[249]/self.frame.high.iloc[:250].max()-1, places=11)
        self.assertEqual(calculate_baselines(self.frame, self.calendar, self.calendar[78])["volume_shock_20"]["reason"],
                         "insufficient_calendar_history")
        self.assertIsNotNone(calculate_baselines(self.frame, self.calendar, self.calendar[79])["volume_shock_20"]["value"])
        self.assert_reference(self.frame, [self.calendar[78], self.calendar[79], self.calendar[248], self.calendar[249]])

    def test_vol_ratio_zero_denominator_and_constant_correlation(self):
        flat = self.frame.copy()
        flat.loc[flat.index >= 359, "close"] = 10.
        result = calculate_baselines(flat, self.calendar, self.calendar[419])
        self.assertEqual(result["vol_ratio_20_60"]["reason"], "zero_volatility_denominator")
        self.assertEqual(result["vol_return_corr_20"]["reason"], "constant_series_correlation")
        self.assertIsNone(result["volatility_20"]["reason"])
        self.assertEqual(result["volatility_20"]["value"], 0.)
        self.assert_reference(flat, [self.calendar[360], self.calendar[419]])

    def test_mf_volume_ratio_zero_downside(self):
        rising = self.frame.copy()
        rising.loc[395:, "close"] = 10.+np.arange(25)
        result = calculate_baselines(rising, self.calendar, self.calendar[419])
        self.assertEqual(result["mf_volume_ratio_20"]["reason"], "zero_downside_volume")
        self.assert_reference(rising, [self.calendar[419]])

    def test_volume_shock_zero_is_valid(self):
        calm = self.frame.copy()
        calm.loc[340:, "volume_shares"] = 1000.
        result = calculate_baselines(calm, self.calendar, self.calendar[419])
        self.assertEqual(result["volume_shock_20"]["value"], 0.)
        self.assertIsNone(result["volume_shock_20"]["reason"])
        self.assertEqual(calculate_baselines(self.frame, self.calendar, self.calendar[419])["volume_shock_20"]["value"], 2.)
        self.assert_reference(calm, [self.calendar[360], self.calendar[419]])

    def test_close_to_vwap_denominator(self):
        zero_volume = self.frame.copy()
        zero_volume.loc[410, "volume_shares"] = 0.
        self.assertEqual(calculate_baselines(zero_volume, self.calendar, self.calendar[419])["close_to_vwap_20"]["reason"],
                         "nonfinite_vwap_denominator")
        zero_amount = self.frame.copy()
        zero_amount.loc[410, "amount_CNY"] = 0.
        self.assertEqual(calculate_baselines(zero_amount, self.calendar, self.calendar[419])["close_to_vwap_20"]["reason"],
                         "nonpositive_vwap_denominator")
        self.assert_reference(zero_volume, [self.calendar[419]])
        self.assert_reference(zero_amount, [self.calendar[419]])

    def test_new_factor_formulas(self):
        result = calculate_baselines(self.frame, self.calendar, self.calendar[419])
        close = self.frame.close.to_numpy()
        returns = close[1:]/close[:-1]-1
        self.assertAlmostEqual(result["momentum_10"]["value"], close[-1]/close[-11]-1, places=11)
        self.assertAlmostEqual(result["reversal_10"]["value"], -(close[-1]/close[-11]-1), places=11)
        self.assertAlmostEqual(result["mom_skip_recent_20"]["value"], close[-6]/close[-21]-1, places=11)
        self.assertAlmostEqual(result["volatility_5"]["value"], statistics.stdev(returns[-5:]), places=11)
        self.assertAlmostEqual(result["downside_vol_20"]["value"],
                               statistics.stdev(np.minimum(returns[-20:], 0)), places=11)
        self.assertAlmostEqual(result["max_daily_ret_20"]["value"], np.max(returns[-20:]), places=11)
        self.assertAlmostEqual(result["range_mean_20"]["value"], .04, places=11)
        self.assertAlmostEqual(result["parkinson_vol_20"]["value"],
                               math.sqrt(math.log(1.02/.98)**2/(4*math.log(2))), places=11)
        self.assertAlmostEqual(result["close_to_vwap_20"]["value"],
                               np.mean(close[-20:]/1000.)-1, places=11)

    def test_identity_date_and_calendar(self):
        cases = [(pd.concat([self.frame, self.frame.iloc[:1]]), self.calendar, self.signals),
                 (self.frame, self.calendar[::-1], self.signals),
                 (self.frame, self.calendar+["2023-01-01"], self.signals),
                 (self.frame, self.calendar, ["2023-01-01"]),
                 (self.frame, self.calendar, [self.calendar[0]]*2)]
        malformed = self.frame.copy()
        malformed.loc[0, "date"] = "20200101"
        cases.append((malformed, self.calendar, self.signals))
        other = self.frame.copy()
        other.loc[0, "symbol"] = "600000.SH"
        cases.append((other, self.calendar, self.signals))
        for frame, calendar, signals in cases:
            with self.assertRaises(ValueError):
                compute_signal_panel(frame, calendar, signals)


if __name__ == "__main__":
    unittest.main()
