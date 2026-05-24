"""RigConfig + rigctld envfile renderer."""

from __future__ import annotations

from pathlib import Path

import pytest

from ft8_appliance.config import AppConfig, OperatorConfig, RigConfig
from ft8_appliance.rig import render_rigctld_envfile, write_rigctld_envfile


def _app(rig: RigConfig | None = None) -> AppConfig:
    return AppConfig(
        operator=OperatorConfig(callsign="DK9XR"),
        rig=rig or RigConfig(),
    )


class TestRigConfigDefaults:
    def test_default_is_ic705(self) -> None:
        cfg = _app()
        assert cfg.rig.model == "ic705"
        assert cfg.rig.hamlib_id == 3085
        assert cfg.rig.effective_max_power_w == 10
        assert cfg.rig.cat_baud == 19200

    def test_explicit_ic7300(self) -> None:
        cfg = _app(RigConfig(model="ic7300"))
        assert cfg.rig.hamlib_id == 3073
        assert cfg.rig.effective_max_power_w == 100

    def test_explicit_power_cap_overrides_default(self) -> None:
        """User can dial down below the rig's stock max (e.g. for QRP work)."""
        cfg = _app(RigConfig(model="ic7300", max_power_w=25))
        assert cfg.rig.effective_max_power_w == 25

    def test_unknown_model_rejected_by_pydantic(self) -> None:
        with pytest.raises(ValueError):
            RigConfig(model="ic9999")  # type: ignore[arg-type]


class TestEnvfileRenderer:
    def test_render_default_ic705(self) -> None:
        content = render_rigctld_envfile(RigConfig())
        assert "RIG_MODEL=3085\n" in content
        assert "RIG_DEVICE=/dev/serial/by-id/usb-Icom_Inc._IC-705-if00\n" in content
        assert "RIG_BAUD=19200\n" in content

    def test_render_ic7300_with_custom_device(self) -> None:
        rig = RigConfig(
            model="ic7300",
            serial_device="/dev/serial/by-id/usb-Icom_Inc._IC-7300-if00",
        )
        content = render_rigctld_envfile(rig)
        assert "RIG_MODEL=3073\n" in content
        assert "RIG_DEVICE=/dev/serial/by-id/usb-Icom_Inc._IC-7300-if00\n" in content

    def test_write_creates_file(self, tmp_path: Path) -> None:
        rig = RigConfig(model="ic7300", cat_baud=115200)
        target = tmp_path / "subdir" / "ft8-rigctld"
        write_rigctld_envfile(rig, target)
        text = target.read_text()
        assert "RIG_MODEL=3073" in text
        assert "RIG_BAUD=115200" in text
