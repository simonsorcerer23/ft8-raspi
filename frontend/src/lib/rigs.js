// Eine Rig-Tabelle fuer alle Komponenten. Spiegelt _RIG_TABLE in
// backend/ft8_appliance/config/models.py (Modell-Schluessel, Werksleistung).
// Vorher hatte jede Komponente ihre eigene Kopie; in der Konfigseite fehlten
// ft817/ft818, der Zugriff auf .max_power_w warf und die Seite blieb leer
// (Audit 14.11.2026, F3).
export const RIGS = {
  ic705:     { name: 'Icom IC-705',            short: 'IC-705',           max_power_w: 10,
               serial_hint: '/dev/serial/by-id/usb-Icom_Inc._IC-705-if00' },
  ic7300:    { name: 'Icom IC-7300',           short: 'IC-7300',          max_power_w: 100,
               serial_hint: '/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller-if00-port0' },
  ic7300mk2: { name: 'Icom IC-7300MK2',        short: 'IC-7300MK2',       max_power_w: 100 },
  ic9700:    { name: 'Icom IC-9700',           short: 'IC-9700',          max_power_w: 100 },
  ic7610:    { name: 'Icom IC-7610',           short: 'IC-7610',          max_power_w: 100 },
  qmx_plus:  { name: 'QRP Labs QMX/QMX+',      short: 'QMX/QMX+',         max_power_w: 5 },
  ft817:     { name: 'Yaesu FT-817 (Digirig)', short: 'FT-817 (Digirig)', max_power_w: 5 },
  ft818:     { name: 'Yaesu FT-818 (Digirig)', short: 'FT-818 (Digirig)', max_power_w: 5 },
};

// Unbekanntes Modell (neuere Box, aeltere Oberflaeche): nie werfen. 5 W ist
// die kleinste Werksleistung der Tabelle — ein Regler bietet dann eher zu
// wenig als zu viel an; die harte Grenze setzt ohnehin das Backend.
const UNBEKANNT_MAX_W = 5;

export function rigKnown(model) {
  return Object.hasOwn(RIGS, model ?? '');
}

export function rigMaxPowerW(model) {
  return rigKnown(model) ? RIGS[model].max_power_w : UNBEKANNT_MAX_W;
}

// Eintrag fuers Modell-Dropdown: "Icom IC-7300 (100 W)".
export function rigLabel(model) {
  return rigKnown(model)
    ? `${RIGS[model].name} (${RIGS[model].max_power_w} W)`
    : String(model ?? '');
}

// Kurzname fuer Panel-Ueberschriften; null bei unbekanntem Modell.
export function rigShortName(model) {
  return rigKnown(model) ? RIGS[model].short : null;
}

// Vorschlag fuer den seriellen Pfad, wenn das Modell einen festen hat.
export function rigSerialHint(model) {
  return rigKnown(model) ? (RIGS[model].serial_hint ?? null) : null;
}

export const RIG_MODELS = Object.keys(RIGS);
