import datetime as dt
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from research.mentor_clean import ROOT, FIELDS, bounds, build, normalize


def row(date="20160104", **changes):
    values = {"证券代码": "000001.SZ", "交易时间": date}
    values.update({column: "10" for column, _ in FIELDS.values()})
    values.update(changes)
    return values


class MentorCleanTests(unittest.TestCase):
    def test_units_and_unknowns(self):
        data, stats = normalize(pd.DataFrame([row(**{"总股本 （万股）": "nan"})]),
                                "000001.SZ", dt.date(2016, 1, 1), dt.date(2021, 12, 31), set())
        self.assertEqual(data.volume_shares.iloc[0], 1000)
        self.assertEqual(data.amount_CNY.iloc[0], 10000)
        self.assertEqual(data.turnover_fraction.iloc[0], .1)
        self.assertEqual(data.float_shares.iloc[0], 100000)
        self.assertTrue(pd.isna(data.total_shares.iloc[0]))
        self.assertEqual(stats["numeric_fault_rows"], 1)

    def test_dates_and_duplicates_across_chunks(self):
        seen = set()
        dates = [row(), row("20160230"), row("20220104"), row("20230104")]
        result, stats = normalize(pd.DataFrame(dates), "000001.SZ", *bounds("2016-01-01", "2021-12-31"), seen)
        self.assertEqual(result.date.tolist(), ["2016-01-04"])
        self.assertEqual(stats["malformed_date_rows"], 1)
        result, stats = normalize(pd.DataFrame([row()]), "000001.SZ", *bounds("2016-01-01", "2021-12-31"), seen)
        self.assertTrue(result.empty)
        self.assertEqual(stats["duplicate_rows"], 1)
        receipt = ROOT / "reports/research/holdout_2022_preregistration_receipt.md"
        for end, allowed in (("2022-01-01", True), ("2023-01-01", False)):
            # 2022 requires the authorized local receipt; 2023+ stays rejected.
            if allowed:
                if receipt.is_file():
                    bounds("2016-01-01", end)
            else:
                with self.assertRaises(ValueError):
                    bounds("2016-01-01", end)

    def test_bad_ohlc_and_identity(self):
        result, stats = normalize(pd.DataFrame([row(**{"最高价": "9", "成交量(手)": "inf"}),
                                               row(**{"证券代码": "000002.SZ"})]),
                                  "000001.SZ", *bounds("2016-01-01", "2021-12-31"), set())
        self.assertIn("invalid_OHLC", result.quality_flags.iloc[0])
        self.assertIn("nonfinite_volume_shares", result.quality_flags.iloc[0])
        self.assertEqual(stats["identity_conflict_rows"], 1)

    def test_atomic_build_reuse_and_changed_input(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            path = source / "000001.SZ.csv"
            pd.DataFrame([row(), row(), row("20170104")]).to_csv(path, encoding="gb18030", index=False)
            result = build("2016-01-01", "2021-12-31", root / "clean", source=source, chunksize=1)
            self.assertEqual(result["rows"], 2)
            self.assertEqual(result["quality_counts"]["duplicate_rows"], 1)
            self.assertEqual(result['excluded_from_research_keys'],[{'symbol':'000001.SZ','date':'2016-01-04','reason':'duplicate_source_key'}])
            self.assertEqual(len(result["outputs"]), 2)
            self.assertFalse(result["readiness"])
            self.assertEqual(build("2016-01-01", "2021-12-31", root / "clean", source=source), result)
            with path.open("a") as stream:
                stream.write("\n")
            with self.assertRaises(ValueError):
                build("2016-01-01", "2021-12-31", root / "clean", source=source)

    def test_unreadable_missing_and_wrong_schema(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "000001.SZ.csv").write_bytes(b"\xff\xfe\xff")
            result = build("2016-01-01", "2021-12-31", root / "bad", source=source)
            self.assertEqual(len(result["errors"]), 1)
            self.assertEqual(result["rows"], 0)
            result = build("2016-01-01", "2021-12-31", root / "missing", ["000002.SZ"], source)
            self.assertEqual(result["errors"][0]["error"], "FileNotFoundError")


if __name__ == "__main__":
    unittest.main()
