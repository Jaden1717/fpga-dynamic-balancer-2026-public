import importlib
import numpy as np
import pytest
from test_sync_csv import ROOT
from test_prepare_tach_order1x import data


def aa():
    assert (ROOT/'sim/golden/angle_antialias.py').is_file(), 'missing anti-alias resampler'
    return importlib.import_module('angle_antialias')


@pytest.mark.parametrize('order',[63,65])
def test_high_order_alias_is_removed_without_changing_low_orders(order):
    turns=4;n=turns*128
    a=2*np.pi*np.arange(n)/128
    dense=256000+128*np.cos(a+.37)+2000*np.cos(9*a-.2)+512*np.cos(order*a+.8)
    want=256000+128*np.cos(a[::2]+.37)+2000*np.cos(9*a[::2]-.2)
    assert np.max(np.abs(dense[::2]-want))>100
    got=aa().decimate_angle128(dense)
    np.testing.assert_allclose(got,want,atol=1e-7)


def test_dc_and_nonperiodic_record_one_x_coefficient_preserved():
    x=256000+np.random.default_rng(42).normal(0,500,128*8)
    y=aa().decimate_angle128(x)
    before=2*np.mean(x*np.exp(-2j*np.pi*np.arange(len(x))/128))
    after=2*np.mean(y*np.exp(-2j*np.pi*np.arange(len(y))/64))
    assert after==pytest.approx(before,abs=1e-9)
    assert y.mean()==pytest.approx(x.mean(),abs=1e-8)


@pytest.mark.parametrize('x',[[0]*127,[0]*129,[float('nan')]*128,[[0]*128]])
def test_invalid_dense_grid_rejected(x):
    with pytest.raises(ValueError):aa().decimate_angle128(x)


def test_timestamp_sampling_density_required():
    t=np.arange(201)/1000
    with pytest.raises(ValueError,match='enough time samples'):
        aa().resample_antialiased(t,np.zeros(len(t)),np.array([0,.1,.2]))


def test_opt_in_preparation_preserves_legacy_and_binds_method(tmp_path):
    data(tmp_path)
    p=importlib.import_module('prepare_tach_order1x')
    legacy=p.prepare(tmp_path,source='fan-tach-2ppr',axis='x',avg_log2=1)
    new=p.prepare(tmp_path,source='fan-tach-2ppr',axis='x',avg_log2=1,resampling='aa128-to64')
    again=p.prepare(tmp_path,source='fan-tach-2ppr',axis='x',avg_log2=1)
    assert legacy==again
    assert len(new['samples_raw'])==128
    assert new['source_sha256']==legacy['source_sha256']
    assert new['window']==legacy['window']
    assert new['preprocessing']['method']=='aa128-to64'
    assert new['preprocessing']['location']=='Python/PS'
    assert new['reference']['amplitude_g']==pytest.approx(.02,abs=3e-5)
    assert new['reference']['phase_deg']==pytest.approx(37,abs=.1)
    assert new['physical_validation_complete'] is False


def test_unknown_method_rejected(tmp_path):
    data(tmp_path)
    p=importlib.import_module('prepare_tach_order1x')
    with pytest.raises(ValueError,match='resampling'):
        p.prepare(tmp_path,source='fan-tach-2ppr',resampling='typo')
