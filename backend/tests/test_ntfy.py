

def test_action_labels_enthalten_kein_komma():
    """Aktions-Strings werden an Kommas zerlegt (``_parse_action``).

    Ein Komma im Knopftext verschiebt daher alle Felder: die Zieladresse
    landet in den Zusatzparametern, ntfy lehnt die Aktion ab. Solange der
    Zerleger so arbeitet, duerfen die Labels keine Kommas enthalten.
    """
    from ft8_appliance.i18n import _MESSAGES

    for name, tabelle in _MESSAGES.items():
        for schluessel, wert in tabelle.items():
            if schluessel.startswith("push.act_") and isinstance(wert, str):
                assert "," not in wert, (
                    f"{name}/{schluessel} enthaelt ein Komma und wuerde die "
                    f"ntfy-Aktion zerreissen: {wert!r}"
                )


def test_parse_action_verliert_felder_bei_komma_im_label():
    """Dokumentiert die Grenze des Zerlegers (siehe Test darueber)."""
    from ft8_appliance.integrations.ntfy import _parse_action

    ok = _parse_action("http, STOP, http://ft8:8000/api/control/stop, method=POST")
    assert ok is not None and ok["url"] == "http://ft8:8000/api/control/stop"

    kaputt = _parse_action("http, STOP, jetzt, http://ft8:8000/api/control/stop")
    assert kaputt is not None and kaputt["url"] != "http://ft8:8000/api/control/stop"
