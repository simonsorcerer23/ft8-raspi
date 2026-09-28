"""Das Self-Update uebernimmt nur Tags mit gueltiger Signatur.

Bis 2026-09-28 checkte scripts/self-update.sh jeden neueren v*-Tag von
GitHub ohne Pruefung aus. Der Test spielt es mit einem Wegwerf-Schluessel in
einem Wegwerf-Repo durch: signierter Tag wird angenommen, unsignierter und
fremd signierter abgelehnt — mit genau dem Befehl, den das Skript benutzt.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[2]
SKRIPT = (WURZEL / "scripts" / "self-update.sh").read_text()

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen fehlt")


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=check, env={"GIT_CONFIG_GLOBAL": "/dev/null",
                                            "GIT_CONFIG_SYSTEM": "/dev/null",
                                            "HOME": str(repo), "PATH": "/usr/bin:/bin"})


def _schluessel(ordner: Path, name: str) -> Path:
    k = ordner / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(k)],
                   check=True)
    return k


def _pruefe(repo: Path, erlaubt: Path, tag: str) -> bool:
    """Genau der Aufruf aus self-update.sh."""
    return _git(repo, "-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={erlaubt}",
                "verify-tag", tag, check=False).returncode == 0


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "--allow-empty", "-m", "a")
    return r


def _signiert(repo: Path, tag: str, key: Path) -> None:
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@x", "-c", "gpg.format=ssh",
         "-c", f"user.signingkey={key}.pub", "tag", "-s", tag, "-m", tag)


def test_signierter_tag_wird_angenommen(repo: Path, tmp_path: Path) -> None:
    k = _schluessel(tmp_path, "release")
    erlaubt = tmp_path / "allowed_signers"
    erlaubt.write_text(f'ft8-release namespaces="git" {(k.with_suffix(".pub")).read_text()}')
    _signiert(repo, "v1.0.0", k)
    assert _pruefe(repo, erlaubt, "v1.0.0")


def test_unsignierter_tag_wird_abgelehnt(repo: Path, tmp_path: Path) -> None:
    k = _schluessel(tmp_path, "release")
    erlaubt = tmp_path / "allowed_signers"
    erlaubt.write_text(f'ft8-release namespaces="git" {(k.with_suffix(".pub")).read_text()}')
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@x", "tag", "-a", "v1.0.1", "-m", "x")
    assert not _pruefe(repo, erlaubt, "v1.0.1")


def test_fremd_signierter_tag_wird_abgelehnt(repo: Path, tmp_path: Path) -> None:
    echt, fremd = _schluessel(tmp_path, "release"), _schluessel(tmp_path, "angreifer")
    erlaubt = tmp_path / "allowed_signers"
    erlaubt.write_text(f'ft8-release namespaces="git" {(echt.with_suffix(".pub")).read_text()}')
    _signiert(repo, "v9.9.9", fremd)
    assert not _pruefe(repo, erlaubt, "v9.9.9")


def test_pruefung_kommt_vor_wartemodus_und_checkout() -> None:
    """Ein abgelehnter Tag darf die Station weder umstellen noch angehalten
    zuruecklassen (der Wartemodus stoppt neue Anrufe)."""
    pruef = SKRIPT.index("verify-tag")
    assert pruef < SKRIPT.index('checking orchestrator idle-state')
    assert pruef < SKRIPT.index('git checkout --quiet "${LATEST_TAG}"')
    block = SKRIPT[pruef - 400:pruef + 300]
    assert "die " in block, "Ablehnung muss abbrechen, nicht nur warnen"


def test_release_signiert_wenn_ein_schluessel_eingerichtet_ist() -> None:
    r = (WURZEL / "scripts" / "release.sh").read_text()
    assert 'run "git tag -s ${TAG}' in r


def test_schluessel_wandert_mit_sicherung_und_wiederherstellung() -> None:
    """Ohne allowed_signers laeuft das Update still ungeprueft. Am 28.09.
    fehlte der Pfad in der Sicherung — eine aus ihr aufgebaute Station haette
    die Pruefung verloren, ohne dass es jemand merkt."""
    backup = (WURZEL / "scripts" / "backup-appliance.sh").read_text()
    restore = (WURZEL / "scripts" / "restore-appliance.sh").read_text()
    assert "/etc/ft8-self-update" in backup.split("PATHS=(", 1)[1].split("\n)", 1)[0]
    assert "etc/ft8-self-update" in restore
    assert "sudo chown -R root:root /etc/ft8-self-update" in restore


def test_install_nimmt_einen_schluessel_und_warnt_ohne() -> None:
    inst = (WURZEL / "deploy" / "install.sh").read_text()
    assert "--release-key)" in inst
    assert "/etc/ft8-self-update/allowed_signers" in inst
    assert "OHNE Signaturpruefung" in inst
    # Kein Schluessel fest im Repo: Forks mit eigenen Releases
    assert "ssh-ed25519 AAAA" not in inst
