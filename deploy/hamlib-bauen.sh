#!/usr/bin/env bash
# Ein neueres hamlib NEBEN das der Distribution bauen: /opt/hamlib-<Version>,
# dazu der feste Verweis /opt/hamlib. Das Paket der Distribution bleibt
# unangetastet; nur Rig-Profile mit neues_hamlib=True (IC-7300MK2) nehmen
# /opt/hamlib/bin/rigctld, s. backend/ft8_appliance/rig/rigctld_envfile.py.
#
#   ./deploy/hamlib-bauen.sh            # Vorgabe-Version
#   ./deploy/hamlib-bauen.sh 4.7.2
#
# Als normaler Benutzer mit sudo aufrufen. Quelle und Pruefsumme kommen von
# der Release-Seite des hamlib-Projekts; stimmt die Summe nicht, bricht es ab.
set -euo pipefail

VERSION="${1:-4.7.2}"
BASIS="https://github.com/Hamlib/Hamlib/releases/download/${VERSION}"
ZIEL="/opt/hamlib-${VERSION}"
BAU="${HOME}/build"

if [[ -x "${ZIEL}/bin/rigctld" ]]; then
    echo "hamlib ${VERSION} liegt schon unter ${ZIEL}"
else
    mkdir -p "${BAU}" && cd "${BAU}"
    rm -rf "hamlib-${VERSION}" "hamlib-${VERSION}.tar.gz" "SHA256SUM-${VERSION}"
    wget -q "${BASIS}/hamlib-${VERSION}.tar.gz" "${BASIS}/SHA256SUM-${VERSION}"
    summe="$(sha256sum "hamlib-${VERSION}.tar.gz" | cut -d' ' -f1)"
    grep -q "${summe}" "SHA256SUM-${VERSION}" || { echo "Pruefsumme stimmt nicht" >&2; exit 1; }
    tar xzf "hamlib-${VERSION}.tar.gz"
    cd "hamlib-${VERSION}"
    ./configure --prefix="${ZIEL}" --without-cxx-binding --disable-static >/dev/null
    nice make -j"$(( $(nproc) > 1 ? $(nproc) - 1 : 1 ))" >/dev/null
    sudo make install >/dev/null
fi

sudo ln -sfn "${ZIEL}" /opt/hamlib
/opt/hamlib/bin/rigctld -V
