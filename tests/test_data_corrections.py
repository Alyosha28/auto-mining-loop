import unittest
from research.data_corrections import applicable_corrections, affected_usage_blocks


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {
                'id': 'SYNTH-DELISTING', 'symbol': '000001.BJ', 'field': 'listed',
                'value': False, 'status': 'VERIFIED',
                'available_at': '2022-04-26T16:00:00+08:00',
                'effective_date': '2022-04-26', 'valid_to': None,
            },
            {
                'id': 'SYNTH-RETROSPECTIVE', 'symbol': '000002.SH', 'field': 'is_st',
                'value': True, 'status': 'VERIFIED',
                'available_at': '2022-06-29T09:00:00+08:00',
                'effective_date': '2022-06-28', 'valid_to': None,
            },
            {
                'id': 'SYNTH-ST-SNAPSHOT', 'symbol': '000003.SZ', 'field': 'is_st',
                'value': True, 'status': 'VERIFIED',
                'available_at': '2016-01-04T08:00:00+08:00',
                'effective_date': '2016-01-04', 'valid_to': '2016-01-05',
            },
        ]

    def test_delisting_evidence_does_not_time_travel_or_apply_before_effective_day(self):
        self.assertEqual(applicable_corrections(self.records, '000001.BJ', '2022-04-26', '2022-04-22T16:00:00+08:00'), {})
        self.assertEqual(applicable_corrections(self.records, '000001.BJ', '2022-04-25', '2022-04-26T16:00:00+08:00'), {})
        self.assertFalse(applicable_corrections(self.records, '000001.BJ', '2022-04-26', '2022-04-26T16:00:00+08:00')['listed']['value'])

    def test_retrospective_explanation_cannot_modify_development_signal(self):
        self.assertEqual(applicable_corrections(self.records, '000002.SH', '2022-06-28', '2022-06-28T16:00:00+08:00'), {})

    def test_bounded_st_snapshot_not_carried_forward(self):
        self.assertTrue(applicable_corrections(self.records, '000003.SZ', '2016-01-04', '2016-01-04T16:00:00+08:00')['is_st']['value'])
        self.assertEqual(applicable_corrections(self.records, '000003.SZ', '2016-01-05', '2016-01-05T16:00:00+08:00'), {})

    def test_usage_block_cannot_become_future_universe_filter(self):
        with self.assertRaises(ValueError):
            affected_usage_blocks([], 'X', '2022-01-01', '2022-01-07', 'universe_selection')

    def test_universe_consumes_verified_overlay(self):
        from research.universe import universe_at
        row = {
            'symbol': '000001.BJ', 'date': '2022-04-26',
            'available_at': '2022-04-26T09:00:00+08:00',
            'listed': True, 'is_st': False, 'suspended': False,
            'asset_type': 'a_share', 'listing_date': '2021-11-15',
        }
        result = universe_at(
            [row], ['000001.BJ'], '2022-04-26T16:00:00+08:00', ['2022-04-26'],
            min_listing_days=1, corrections=self.records,
        )
        self.assertEqual(result[0]['reason'], 'not_listed')
        self.assertEqual(result[0]['correction_evidence_ids'], ['SYNTH-DELISTING'])


if __name__ == '__main__':
    unittest.main()
