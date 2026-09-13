

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


def test_clear_wird_wahrheitswert_nicht_zeichenkette():
    """ntfy weist die GANZE Nachricht ab, wenn clear ein String ist.

    2026-09-10: Der Zerleger machte aus jedem Zusatzparameter Text, also auch
    aus ``clear=true`` die Zeichenkette "true". ntfy antwortet darauf mit
    "400 Bad Request — request body must be valid JSON" und verwirft Titel,
    Text und Knopf. Betroffen war jeder Push mit einem "Sperre loesen"-Knopf,
    also ausgerechnet die dringenden: TX gesperrt, Rig verstellt, Waechter
    ausgeloest. Sie kamen auf dem Handy nie an. Aufgefallen, als Raymond das
    Rig verstellte und der dial_guard-Push wieder abgelehnt wurde.
    """
    from ft8_appliance.integrations.ntfy import _parse_action

    a = _parse_action(
        "http, Sperre lösen, http://ft8:8000/api/control/reset-lock?token=X, "
        "method=POST, clear=true"
    )
    assert a is not None
    assert a["clear"] is True, "clear muss ein Wahrheitswert sein, keine Zeichenkette"
    assert isinstance(a["method"], str)


def test_body_bleibt_zeichenkette():
    """ntfy reicht body unveraendert an die Ziel-URL weiter.

    Unsere Steuer-Endpunkte erwarten dort JSON *als Text* — wuerde der
    Zerleger daraus ein Objekt machen, kaeme beim Endpunkt etwas anderes an.
    """
    from ft8_appliance.integrations.ntfy import _parse_action

    a = _parse_action(
        'http, Zurück auf 70 W, http://ft8:8000/api/control/tx-power, method=POST, '
        'headers.content-type=application/json, body={"watts":70}'
    )
    assert a is not None
    assert a["body"] == '{"watts":70}'
    assert isinstance(a["body"], str)
    assert a["headers"]["content-type"] == "application/json"


def test_orchestrator_baut_keine_aktionsknoepfe_mehr():
    """Seit 2026-09-13 verschickt die Station Meldungen ohne Knoepfe.

    Die Knoepfe trugen den Steuer-Token als Klartext in der Adresse, und
    das ntfy-Topic (``ft8-dk9xr``) liegt auf einem oeffentlichen Dienst,
    wo jeder jedes Topic abonnieren kann. Benutzt wurden sie nie — in den
    Aufzeichnungen kein einziger Aufruf.

    Der Test sperrt den Weg zurueck: Taucht wieder eine Aktions-Vorlage
    im Orchestrator auf, faellt er.
    """
    import pathlib
    import re

    import ft8_appliance.runtime.orchestrator as _orch

    src = pathlib.Path(_orch.__file__).read_text()
    vorlagen = re.findall(r'"(http, [^"]+)"', src) + re.findall(r"'(http, [^']+)'", src)
    assert not vorlagen, f"Aktions-Vorlagen wieder da: {vorlagen[:3]}"
    assert "_tok_actions" not in src, "Token-Anhaengsel wieder da"
