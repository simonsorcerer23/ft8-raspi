"""Der ALC-Regler meldet sich, wenn er im Sweet-Spot steht.

Bis 2026-09-12 lief dieser Pfad nur auf DEBUG. Der Regler stand seit Tagen
bei gain 0,29 — ob "konvergiert" oder "tot", war von aussen nicht zu sehen.
"""
from types import SimpleNamespace
from ft8_appliance.runtime import orchestrator as om
from ft8_appliance.runtime.orchestrator import Orchestrator

def _o():
    return SimpleNamespace(
        _tx_alc_samples=[0], _tx_pwr_samples=[0.60], _last_alc_pct=0,
        _last_rig=SimpleNamespace(rfpower_norm=0.698), _audio_gain=0.29,
        _pwr_integrator=0.0, _alc_sweetspot_gemeldet_at=0.0,
        config=SimpleNamespace(operating=SimpleNamespace(
            alc_safety_threshold=40, alc_safety_factor=0.7, pwr_target_ratio=0.8,
            alc_target_pct=15, alc_deadband_pct=5, gain_loop_kp=0.5, gain_loop_ki=0.1,
            pwr_regime_max_delta=0.05)),
        _maybe_persist_runtime_state=lambda *a, **k: None,
    )

def test_sweetspot_meldet_sich_einmal_je_stunde(monkeypatch):
    infos = []
    monkeypatch.setattr(om.log, "info", lambda msg, *a, **k: infos.append(msg % a if a else msg))
    o = _o()
    Orchestrator._apply_burst_loop_update(o)
    assert any("Sweet-Spot gehalten" in m for m in infos), infos
    assert o._audio_gain == 0.29, "im Sweet-Spot darf nicht geregelt werden"
    # zweiter Burst kurz danach: keine zweite Meldung
    o._tx_alc_samples=[0]; o._tx_pwr_samples=[0.60]
    Orchestrator._apply_burst_loop_update(o)
    assert sum("Sweet-Spot gehalten" in m for m in infos) == 1

def test_unterhalb_der_schwelle_kein_sweetspot(monkeypatch):
    infos = []
    monkeypatch.setattr(om.log, "info", lambda msg, *a, **k: infos.append(msg % a if a else msg))
    o = _o(); o._tx_pwr_samples=[0.40]   # < 0,8 x 0,698 = 0,558 → PWR-Regime, regelt hoch
    Orchestrator._apply_burst_loop_update(o)
    assert not any("Sweet-Spot gehalten" in m for m in infos)
    assert o._audio_gain > 0.29
