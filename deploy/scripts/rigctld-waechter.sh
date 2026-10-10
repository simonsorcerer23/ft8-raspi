#!/bin/sh
# rigctld starten und beenden, sobald das Rig vom USB verschwindet.
#
# Wird das Rig ausgeschaltet, verschwindet seine serielle Schnittstelle. Ein
# laufendes rigctld bleibt dann mit einer toten Verbindung stehen (10.10.2026:
# lief nach dem Ausschalten einfach weiter) oder beendet sich irgendwann
# sauber (02.10.2026) — in beiden Faellen spricht nach dem Wiedereinschalten
# niemand mit dem Rig. Dieser Waechter beendet rigctld, wenn das Geraet fehlt;
# systemd (Restart=always) versucht es dann alle zehn Sekunden neu, und der
# Start gelingt erst wieder, wenn das Geraet da ist.
#
# Die Variablen kommen aus /etc/default/ft8-rigctld (EnvironmentFile).
# $RIG_PTT_ARGS steht absichtlich ohne Anfuehrungszeichen: Die Shell teilt es
# in Woerter (--ptt-type=RTS --ptt-file=...); bei CAT-PTT ist es leer.

BIN="${RIGCTLD:-/usr/bin/rigctld}"

if [ ! -e "$RIG_DEVICE" ]; then
    echo "Rig nicht am USB ($RIG_DEVICE) - warte"
    exit 75
fi

# shellcheck disable=SC2086
"$BIN" -m "$RIG_MODEL" -r "$RIG_DEVICE" -s "$RIG_BAUD" $RIG_PTT_ARGS -t 4532 -T 127.0.0.1 &
pid=$!
trap 'kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; exit 0' TERM INT

while kill -0 "$pid" 2>/dev/null; do
    if [ ! -e "$RIG_DEVICE" ]; then
        echo "Rig vom USB verschwunden - rigctld wird beendet"
        kill "$pid" 2>/dev/null
        wait "$pid" 2>/dev/null
        exit 75
    fi
    sleep 5 &
    wait $!
done
wait "$pid"
