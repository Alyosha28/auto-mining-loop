import unittest
import numpy as np
import pandas as pd
from research.local_quote_library import compute_quote_factors,weekly_signals
from research.price_volume_batch import compute_signal_panel


class LocalQuoteLibraryTests(unittest.TestCase):
    def data(self):
        dates=pd.bdate_range('2021-01-04',periods=130).strftime('%Y-%m-%d').tolist()
        price=10+np.sin(np.arange(130)/5)*.4+np.arange(130)*.01
        f=pd.DataFrame({'date':dates,'symbol':'000001.SZ','open':price,'high':price+.2,'low':price-.2,'close':price,
            'reference_preclose':np.r_[price[0],price[:-1]],'volume_shares':1e6+np.arange(130)*100,
            'amount_CNY':price*1e6,'turnover_fraction':.02,'total_market_cap_CNY':1e10})
        signals=weekly_signals(dates,dates[0],dates[-1]);st={d:False for d in dates}
        return f,dates,signals,st

    def test_wide_is_exact_legacy_values(self):
        f,dates,signals,_=self.data()
        long=compute_signal_panel(f,dates,signals).pivot(index='date',columns='candidate_id',values='value')
        wide=compute_signal_panel(f,dates,signals,wide=True).set_index('date').drop(columns='symbol')
        pd.testing.assert_frame_equal(long.sort_index(axis=1),wide.sort_index(axis=1),check_names=False)

    def test_flat_active_days_are_a_valid_state(self):
        f,dates,signals,st=self.data();f.loc[65:95,['open','high','low','close']]=10
        out=compute_quote_factors(f,dates,signals,[],st).set_index('date')
        selected=[d for d in signals if dates[86]<=d<=dates[94]]
        self.assertTrue(out.loc[selected,'intraday_flat_range_share_20'].eq(1).all())

    def test_missing_source_is_not_flat_state(self):
        f,dates,signals,st=self.data();f.loc[90,'volume_shares']=np.nan
        out=compute_quote_factors(f,dates,signals,[],st).set_index('date')
        selected=[d for d in signals if dates[90]<=d<=dates[108]]
        self.assertTrue(out.loc[selected,'intraday_flat_range_share_20'].isna().all())

    def test_future_labels_do_not_change_research_pool(self):
        f,dates,signals,st=self.data();a=compute_quote_factors(f,dates,signals,[],st)
        f.loc[100:,'open']*=2;f.loc[100:,'high']=f.loc[100:,'open']+.2
        b=compute_quote_factors(f,dates,signals,[],st)
        before=a.date.lt(dates[100]);pd.testing.assert_series_equal(a.loc[before,'research_mask'],b.loc[before,'research_mask'])

    def test_st_is_previous_session_and_unknown_is_not_false(self):
        f,dates,signals,st=self.data();date=signals[15];prior=dates[dates.index(date)-1]
        st[date]=True;out=compute_quote_factors(f,dates,signals,[],st).set_index('date')
        self.assertTrue(out.loc[date,'research_mask'])
        st[prior]=True;out=compute_quote_factors(f,dates,signals,[],st).set_index('date')
        self.assertEqual(out.loc[date,'universe_reason'],'ST')
        del st[prior];out=compute_quote_factors(f,dates,signals,[],st).set_index('date')
        self.assertEqual(out.loc[date,'universe_reason'],'unknown_ST')

    def test_actions_mask_labels_not_adjusted_features(self):
        f,dates,signals,st=self.data();a=compute_quote_factors(f,dates,signals,[],st)
        b=compute_quote_factors(f,dates,signals,[dates[86]],st)
        pd.testing.assert_series_equal(a.research_mask,b.research_mask)
        self.assertGreater(b.label_reason.eq('action_horizon_raw_return_unfit').sum(),0)
        pd.testing.assert_series_equal(a.momentum_20,b.momentum_20)

    def test_reference_preclose_adjusts_price_features(self):
        f,dates,signals,st=self.data();a=compute_quote_factors(f,dates,signals,[],st)
        event=pd.Timestamp(dates[86]).date().isoformat()
        changed=f.copy();idx=changed.index[changed.date.eq(event)][0]
        changed.loc[idx,'reference_preclose']=changed.loc[idx,'reference_preclose']-0.5
        b=compute_quote_factors(changed,dates,signals,[],st)
        self.assertFalse(np.allclose(a.momentum_20.fillna(0),b.momentum_20.fillna(0)))

    def test_partial_final_week_excluded(self):
        self.assertEqual(weekly_signals(['2025-09-19','2025-09-22','2025-09-23','2025-09-24'],'2025-09-01','2025-09-24'),['2025-09-19'])


if __name__=='__main__':unittest.main()
