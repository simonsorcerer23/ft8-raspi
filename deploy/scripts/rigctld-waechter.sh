#!/bin/sh
# rigctld starten und beenden, sobald das Rig vom USB verschwindet.
#
# Wird das Rig ausgeschaltet, verschwindet seine serielle Schnittstelle. Ein
# laufendes rigctld bleibt dann mit einer toten Verbindung stehen (10.10.2026:
# lief nach dem Ausschalten einfach weiter) oder beendet sich irgendwann
# sauber (02.10.2026) — in beiden Faellen spricht nach dem Wiedereinschalten
# niemand mit dem Rig. Dieser Waechter beendet rigctld, wenn das Geraet fehlt,
# wartet still, bis es wieder da ist, und startet rigctld dann neu.
#
# Die Variablen kommen aus /etc/default/ft8-rigctld (EnvironmentFile).
# $RIG_PTT_ARGS steht absichtlich ohne Anfuehrungszeichen: Die Shell teilt es
# in Woerter (--ptt-type=RTS --ptt-file=...); bei CAT-PTT ist es leer.

BIN="${RIGCTLD:-/usr/bin/rigctld}"
pid=""
trap '[ -n "$pid" ] && kill "$pid" 2>/dev/null; wait 2>/dev/null; exit 0' TERM INT

pause() {            # unterbrechbar, damit TERM sofort wirkt
    sleep "$1" &
    wait $!
}

while :; do
    # Still warten, bis das Rig am USB ist — eine Zeile, keine alle paar
    # Sekunden (ein Neustart-Karussell ueber systemd schriebe rund 40 000
    # Zeilen je Tag ins Journal).
    if [ ! -e "$RIG_DEVICE" ]; then
        echo "Rig nicht am USB ($RIG_DEVICE) - warte"
        while [ ! -e "$RIG_DEVICE" ]; do pause 3; done
        echo "Rig wieder am USB"
        pause 2      # dem Geraet Zeit zum Hochfahren lassen
    fi

    # shellcheck disable=SC2086
    "$BIN" -m "$RIG_MODEL" -r "$RIG_DEVICE" -s "$RIG_BAUD" $RIG_PTT_ARGS -t 4532 -T 127.0.0.1 &
    pid=$!

    while kill -0 "$pid" 2>/dev/null; do
        if [ ! -e "$RIG_DEVICE" ]; then
            echo "Rig vom USB verschwunden - rigctld wird beendet"
            kill "$pid" 2>/dev/null
            wait "$pid" 2>/dev/null
            pid=""
            continue 2
        fi
        pause 3
    done

    # rigctld ist von selbst gegangen, obwohl das Rig da ist: systemd soll
    # es sehen und neu starten (Restart=always).
    wait "$pid"
    exit $?
done
