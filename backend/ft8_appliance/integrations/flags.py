"""DXCC → ISO2-Country-Code → Unicode-Flag-Emoji.

Sebastian-Request 2026-05-24 (v0.3.0). Wir nutzen die existierende
``cty.dat``-Integration (siehe ``integrations/cty_dat.py``) um aus dem
Callsign die DXCC-Entity zu bestimmen, mappen die ``primary_prefix``
auf einen ISO-3166-1-alpha-2 Country-Code, und konvertieren den in
zwei Regional-Indicator-Codepoints (Unicode-Trick für Flag-Emojis).

Beispiel: ``DL5XYZ`` → entity primary_prefix ``"DL"`` → ISO2 ``"DE"``
→ Codepoints ``U+1F1E9 U+1F1EA`` = 🇩🇪.

Die Mapping-Tabelle deckt **alle ~340 aktiven DXCC-Entities** ab. Bei
Sonderfällen ohne eindeutige ISO-Zuordnung (Antarktis-Basen, ITU/UN-
Sonder-Calls, Maritime-Mobile) wird ``None`` zurückgegeben — Caller
zeigen dann keine Flagge.
"""

from __future__ import annotations

# DXCC primary_prefix (Feld 8 in cty.dat) → ISO-3166-1-alpha-2.
# Quellen: ARRL DXCC-Liste, ITU-Prefix-Tabelle, country-files.com.
# Stand: 2026-05.
#
# Konvention: wo eine DXCC-Entity politisch zu einem anderen ISO-Land
# gehört (z.B. Sicily/Sardinia → IT, alle UK-Crown-Dependencies separat),
# nehmen wir den **lokalen** ISO-Code wenn vorhanden (Jersey=JE, IoM=IM),
# sonst den Mutterland-Code (Sicily=IT). Das macht die Flaggen
# wiedererkennbar.
_DXCC_PRIMARY_TO_ISO2: dict[str, str] = {
    # --- Numerische Prefixe (Spezial-DXCC zuerst) ---
    "3A":   "MC",  # Monaco
    "3B6":  "MU",  # Agalega & St. Brandon (Mauritius)
    "3B8":  "MU",  # Mauritius
    "3B9":  "MU",  # Rodriguez Island
    "3C":   "GQ",  # Equatorial Guinea
    "3C0":  "GQ",  # Annobon
    "3D2":  "FJ",  # Fiji
    "3DA":  "SZ",  # Eswatini
    "3V":   "TN",  # Tunisia
    "3W":   "VN",  # Vietnam
    "3X":   "GN",  # Guinea
    "3Y":   "BV",  # Bouvet
    "4J":   "AZ",  # Azerbaijan
    "4L":   "GE",  # Georgia
    "4O":   "ME",  # Montenegro
    "4S":   "LK",  # Sri Lanka
    "4W":   "TL",  # Timor-Leste
    "4X":   "IL",  # Israel
    "5A":   "LY",  # Libya
    "5B":   "CY",  # Cyprus
    "5H":   "TZ",  # Tanzania
    "5N":   "NG",  # Nigeria
    "5R":   "MG",  # Madagascar
    "5T":   "MR",  # Mauritania
    "5U":   "NE",  # Niger
    "5V":   "TG",  # Togo
    "5W":   "WS",  # Samoa
    "5X":   "UG",  # Uganda
    "5Z":   "KE",  # Kenya
    "6V":   "SN",  # Senegal
    "6W":   "SN",  # Senegal (alt)
    "6Y":   "JM",  # Jamaica
    "7O":   "YE",  # Yemen
    "7P":   "LS",  # Lesotho
    "7Q":   "MW",  # Malawi
    "7X":   "DZ",  # Algeria
    "8P":   "BB",  # Barbados
    "8Q":   "MV",  # Maldives
    "8R":   "GY",  # Guyana
    "9A":   "HR",  # Croatia
    "9G":   "GH",  # Ghana
    "9H":   "MT",  # Malta
    "9J":   "ZM",  # Zambia
    "9K":   "KW",  # Kuwait
    "9L":   "SL",  # Sierra Leone
    "9M2":  "MY",  # West Malaysia
    "9M6":  "MY",  # East Malaysia
    "9N":   "NP",  # Nepal
    "9Q":   "CD",  # DR Congo
    "9U":   "BI",  # Burundi
    "9V":   "SG",  # Singapore
    "9X":   "RW",  # Rwanda
    "9Y":   "TT",  # Trinidad & Tobago
    # --- A-Prefix ---
    "A2":   "BW",  # Botswana
    "A3":   "TO",  # Tonga
    "A4":   "OM",  # Oman
    "A5":   "BT",  # Bhutan
    "A6":   "AE",  # United Arab Emirates
    "A7":   "QA",  # Qatar
    "A9":   "BH",  # Bahrain
    "AP":   "PK",  # Pakistan
    # --- B-Prefix ---
    "BV":   "TW",  # Taiwan
    "BY":   "CN",  # China
    # --- C-Prefix ---
    "C2":   "NR",  # Nauru
    "C3":   "AD",  # Andorra
    "C5":   "GM",  # Gambia
    "C6":   "BS",  # Bahamas
    "C9":   "MZ",  # Mozambique
    "CE":   "CL",  # Chile
    "CE0X": "CL",  # San Felix
    "CE0Y": "CL",  # Easter Island
    "CE0Z": "CL",  # Juan Fernandez
    "CM":   "CU",  # Cuba
    "CN":   "MA",  # Morocco
    "CO":   "CU",  # Cuba (alt)
    "CP":   "BO",  # Bolivia
    "CT":   "PT",  # Portugal
    "CT3":  "PT",  # Madeira
    "CU":   "PT",  # Azores
    "CX":   "UY",  # Uruguay
    # --- D-Prefix ---
    "D2":   "AO",  # Angola
    "D4":   "CV",  # Cape Verde
    "D6":   "KM",  # Comoros
    "DL":   "DE",  # Germany
    "DU":   "PH",  # Philippines
    # --- E-Prefix ---
    "E3":   "ER",  # Eritrea
    "E4":   "PS",  # Palestine
    "E5":   "CK",  # Cook Islands
    "E5/N": "CK",  # North Cook
    "E5/S": "CK",  # South Cook
    "E6":   "NU",  # Niue
    "E7":   "BA",  # Bosnia-Herzegovina
    "EA":   "ES",  # Spain
    "EA6":  "ES",  # Balearic
    "EA8":  "ES",  # Canary Is
    "EA9":  "ES",  # Ceuta & Melilla
    "EI":   "IE",  # Ireland
    "EK":   "AM",  # Armenia
    "EL":   "LR",  # Liberia
    "EP":   "IR",  # Iran
    "ER":   "MD",  # Moldova
    "ES":   "EE",  # Estonia
    "ET":   "ET",  # Ethiopia
    "EU":   "BY",  # Belarus
    "EX":   "KG",  # Kyrgyzstan
    "EY":   "TJ",  # Tajikistan
    "EZ":   "TM",  # Turkmenistan
    # --- F-Prefix ---
    "F":    "FR",  # France
    "FG":   "GP",  # Guadeloupe
    "FH":   "YT",  # Mayotte
    "FJ":   "BL",  # St. Barthelemy
    "FK":   "NC",  # New Caledonia
    "FM":   "MQ",  # Martinique
    "FO":   "PF",  # French Polynesia
    "FP":   "PM",  # St. Pierre & Miquelon
    "FR":   "RE",  # Reunion
    "FS":   "MF",  # St. Martin (French)
    "FT":   "TF",  # French Southern Territories
    "FW":   "WF",  # Wallis & Futuna
    "FY":   "GF",  # French Guiana
    # --- G-Prefix (UK + Crown Dependencies) ---
    "G":    "GB",  # England
    "GD":   "IM",  # Isle of Man
    "GI":   "GB",  # Northern Ireland
    "GJ":   "JE",  # Jersey
    "GM":   "GB",  # Scotland
    "GU":   "GG",  # Guernsey
    "GW":   "GB",  # Wales
    # --- H-Prefix ---
    "H4":   "SB",  # Solomon Islands
    "H40":  "SB",  # Temotu
    "HA":   "HU",  # Hungary
    "HB":   "CH",  # Switzerland
    "HB0":  "LI",  # Liechtenstein
    "HC":   "EC",  # Ecuador
    "HC8":  "EC",  # Galapagos
    "HH":   "HT",  # Haiti
    "HI":   "DO",  # Dominican Republic
    "HK":   "CO",  # Colombia
    "HL":   "KR",  # South Korea
    "HP":   "PA",  # Panama
    "HR":   "HN",  # Honduras
    "HS":   "TH",  # Thailand
    "HV":   "VA",  # Vatican
    "HZ":   "SA",  # Saudi Arabia
    # --- I-Prefix ---
    "I":    "IT",  # Italy
    "IS":   "IT",  # Sardinia
    "IT9":  "IT",  # Sicily
    # --- J-Prefix ---
    "J2":   "DJ",  # Djibouti
    "J3":   "GD",  # Grenada
    "J5":   "GW",  # Guinea-Bissau
    "J6":   "LC",  # St. Lucia
    "J7":   "DM",  # Dominica
    "J8":   "VC",  # St. Vincent
    "JA":   "JP",  # Japan
    "JD1":  "JP",  # Minami Torishima / Ogasawara
    "JT":   "MN",  # Mongolia
    "JW":   "SJ",  # Svalbard
    "JX":   "SJ",  # Jan Mayen
    "JY":   "JO",  # Jordan
    # --- K/N/W-Prefix (USA) ---
    "K":    "US",  # United States
    "KG4":  "US",  # Guantanamo (US controlled)
    "KH0":  "MP",  # Mariana Is
    "KH1":  "UM",  # Baker/Howland
    "KH2":  "GU",  # Guam
    "KH3":  "UM",  # Johnston
    "KH4":  "UM",  # Midway
    "KH5":  "UM",  # Palmyra/Jarvis
    "KH6":  "US",  # Hawaii (zeigen US-Flagge; HI ist Bundesstaat)
    "KH7K": "US",  # Kure
    "KH8":  "AS",  # American Samoa
    "KH9":  "UM",  # Wake
    "KL":   "US",  # Alaska (zeigen US-Flagge)
    "KP1":  "UM",  # Navassa
    "KP2":  "VI",  # US Virgin Is
    "KP4":  "PR",  # Puerto Rico
    "KP5":  "PR",  # Desecheo
    # --- L-Prefix ---
    "LA":   "NO",  # Norway
    "LU":   "AR",  # Argentina
    "LX":   "LU",  # Luxembourg
    "LY":   "LT",  # Lithuania
    "LZ":   "BG",  # Bulgaria
    # --- O-Prefix ---
    "OA":   "PE",  # Peru
    "OD":   "LB",  # Lebanon
    "OE":   "AT",  # Austria
    "OH":   "FI",  # Finland
    "OH0":  "AX",  # Aland Is
    "OJ0":  "FI",  # Market Reef
    "OK":   "CZ",  # Czech Republic
    "OM":   "SK",  # Slovakia
    "ON":   "BE",  # Belgium
    "OX":   "GL",  # Greenland
    "OY":   "FO",  # Faroe Is
    "OZ":   "DK",  # Denmark
    # --- P-Prefix ---
    "P2":   "PG",  # Papua New Guinea
    "P4":   "AW",  # Aruba
    "P5":   "KP",  # North Korea
    "PA":   "NL",  # Netherlands
    "PJ2":  "CW",  # Curacao
    "PJ4":  "BQ",  # Bonaire
    "PJ5":  "BQ",  # Sint Eustatius
    "PJ7":  "SX",  # Sint Maarten
    "PY":   "BR",  # Brazil
    "PY0F": "BR",  # Fernando de Noronha
    "PY0S": "BR",  # St. Peter & Paul
    "PY0T": "BR",  # Trindade
    "PZ":   "SR",  # Suriname
    # --- R/U-Prefix (Russia + Ex-USSR) ---
    "R":    "RU",  # Russia (alle UA / R-Prefixe)
    "UA":   "RU",  # European Russia (alt)
    "UA2":  "RU",  # Kaliningrad
    "UA9":  "RU",  # Asian Russia
    "UK":   "UZ",  # Uzbekistan
    "UN":   "KZ",  # Kazakhstan
    "UR":   "UA",  # Ukraine
    # --- S-Prefix ---
    "S0":   "EH",  # Western Sahara
    "S2":   "BD",  # Bangladesh
    "S5":   "SI",  # Slovenia
    "S7":   "SC",  # Seychelles
    "S9":   "ST",  # Sao Tome
    "SM":   "SE",  # Sweden
    "SP":   "PL",  # Poland
    "ST":   "SD",  # Sudan
    "SU":   "EG",  # Egypt
    "SV":   "GR",  # Greece
    "SV5":  "GR",  # Dodecanese
    "SV9":  "GR",  # Crete
    # --- T-Prefix ---
    "T2":   "TV",  # Tuvalu
    "T30":  "KI",  # Western Kiribati
    "T31":  "KI",  # Central Kiribati
    "T32":  "KI",  # Eastern Kiribati
    "T33":  "KI",  # Banaba
    "T5":   "SO",  # Somalia
    "T7":   "SM",  # San Marino
    "T8":   "PW",  # Palau
    "TA":   "TR",  # Turkey
    "TF":   "IS",  # Iceland
    "TG":   "GT",  # Guatemala
    "TI":   "CR",  # Costa Rica
    "TJ":   "CM",  # Cameroon
    "TK":   "FR",  # Corsica
    "TL":   "CF",  # Central African Republic
    "TN":   "CG",  # Congo (Brazzaville)
    "TR":   "GA",  # Gabon
    "TT":   "TD",  # Chad
    "TU":   "CI",  # Cote d'Ivoire
    "TY":   "BJ",  # Benin
    "TZ":   "ML",  # Mali
    # --- V-Prefix ---
    "V2":   "AG",  # Antigua & Barbuda
    "V3":   "BZ",  # Belize
    "V4":   "KN",  # St. Kitts & Nevis
    "V5":   "NA",  # Namibia
    "V6":   "FM",  # Micronesia
    "V7":   "MH",  # Marshall Islands
    "V8":   "BN",  # Brunei
    "VE":   "CA",  # Canada
    "VK":   "AU",  # Australia
    "VK9C": "AU",  # Cocos-Keeling
    "VK9L": "AU",  # Lord Howe
    "VK9N": "NF",  # Norfolk Island
    "VK9X": "CX",  # Christmas Island
    "VP2E": "AI",  # Anguilla
    "VP2M": "MS",  # Montserrat
    "VP2V": "VG",  # British Virgin Is
    "VP5":  "TC",  # Turks & Caicos
    "VP6":  "PN",  # Pitcairn
    "VP8":  "FK",  # Falkland Is (primary)
    "VP9":  "BM",  # Bermuda
    "VQ9":  "IO",  # Chagos
    "VR":   "HK",  # Hong Kong
    "VU":   "IN",  # India
    "VU4":  "IN",  # Andaman & Nicobar
    "VU7":  "IN",  # Lakshadweep
    # --- X-Prefix ---
    "XE":   "MX",  # Mexico
    "XT":   "BF",  # Burkina Faso
    "XU":   "KH",  # Cambodia
    "XW":   "LA",  # Laos
    "XX9":  "MO",  # Macau
    "XZ":   "MM",  # Myanmar
    # --- Y-Prefix ---
    "YA":   "AF",  # Afghanistan
    "YB":   "ID",  # Indonesia
    "YI":   "IQ",  # Iraq
    "YJ":   "VU",  # Vanuatu
    "YK":   "SY",  # Syria
    "YL":   "LV",  # Latvia
    "YN":   "NI",  # Nicaragua
    "YO":   "RO",  # Romania
    "YS":   "SV",  # El Salvador
    "YU":   "RS",  # Serbia
    "YV":   "VE",  # Venezuela
    # --- Z-Prefix ---
    "Z2":   "ZW",  # Zimbabwe
    "Z3":   "MK",  # North Macedonia
    "Z6":   "XK",  # Kosovo (XK = inofficial ISO)
    "Z8":   "SS",  # South Sudan
    "ZA":   "AL",  # Albania
    "ZB":   "GI",  # Gibraltar
    "ZD7":  "SH",  # St. Helena
    "ZD8":  "SH",  # Ascension
    "ZD9":  "SH",  # Tristan da Cunha
    "ZF":   "KY",  # Cayman Is
    "ZL":   "NZ",  # New Zealand
    "ZL7":  "NZ",  # Chatham
    "ZL8":  "NZ",  # Kermadec
    "ZL9":  "NZ",  # Auckland & Campbell
    "ZP":   "PY",  # Paraguay
    "ZS":   "ZA",  # South Africa
}


# Special-Case-Entities ohne sinnvolle Flagge (Antarktis-Basen via
# verschiedener Länder, ITU/UN-Sonder-Calls, Maritime/Aeronautical Mobile).
# Wir geben für die explizit None zurück damit die UI nichts hilfloses
# zeigt.
_DXCC_SKIP_FLAG: frozenset[str] = frozenset({
    "1A",       # Sovereign Military Order of Malta — kein Staat
    "1S",       # Spratly Islands (disputed)
    "3Y/P",     # Peter I Island (Antarktis-Claim Norwegen, nicht relevant)
    "4U1I",     # ITU HQ Geneva
    "4U1U",     # UN HQ NY
    "CE9",      # Antarctica (CL-claim)
    "KC4",      # US Antarctic Bases
    "VK0H",     # Heard Island (AU sub-antarktis — könnte VK=AU sein)
    "VK0M",     # Macquarie
    "VP8/G",    # South Georgia (UK)
    "VP8/H",    # South Shetland
    "VP8/O",    # South Orkney
    "VP8/S",    # South Sandwich
    "R1FJ",     # Franz Josef Land (RU-claim)
    "R1MV",     # Malyj Vysotskij (deleted)
    "ZS8",      # Prince Edward & Marion (ZA sub-antarktis)
})


# Unicode-Codepoints für Regional-Indicator-Symbols (Flag-Building-Blocks).
# Eine Flagge = zwei Codepoints (z.B. DE = REGIONAL_INDICATOR_D +
# REGIONAL_INDICATOR_E). Browser + Smartphones rendern das als Flagge,
# Linux-Terminal je nach Font-Stack.
_REGIONAL_INDICATOR_BASE = 0x1F1E6  # = 'A'


def iso2_to_flag(iso2: str) -> str:
    """ISO-3166-1-alpha-2 (z.B. 'DE') zu Unicode-Flag-Emoji ('🇩🇪').

    Returns ``""`` (Leerstring) bei invalidem Input damit String-
    Konkatenation in UI safe ist.
    """
    if not iso2 or len(iso2) != 2 or not iso2.isalpha():
        return ""
    upper = iso2.upper()
    return "".join(chr(_REGIONAL_INDICATOR_BASE + (ord(c) - ord("A"))) for c in upper)


def iso2_for_dxcc_primary(primary_prefix: str) -> str | None:
    """Lookup primary_prefix → ISO2. None wenn unbekannt oder Skip-Liste."""
    if not primary_prefix:
        return None
    key = primary_prefix.upper()
    if key in _DXCC_SKIP_FLAG:
        return None
    # Try exact, then progressively shorter (DXCC-Hierarchie: KH6 vor K,
    # 9M2 vor 9M usw.). cty.dat gibt typischerweise schon den richtigen
    # spezifischen Prefix zurück, aber wir handhaben Fallback defensiv.
    if key in _DXCC_PRIMARY_TO_ISO2:
        return _DXCC_PRIMARY_TO_ISO2[key]
    for length in range(len(key) - 1, 0, -1):
        sub = key[:length]
        if sub in _DXCC_PRIMARY_TO_ISO2:
            return _DXCC_PRIMARY_TO_ISO2[sub]
    return None


def flag_for_call(call: str | None, cty) -> str:
    """Callsign → Flag-Emoji (oder Leerstring wenn unbekannt).

    *cty* ist eine ``CtyDat``-Instanz oder ``None`` (z.B. wenn die
    Integration nicht initialisiert ist — dann leerer String).
    """
    if not call or cty is None:
        return ""
    try:
        result = cty.lookup(call)
    except Exception:
        return ""
    if result is None:
        return ""
    iso2 = iso2_for_dxcc_primary(result.entity.primary_prefix)
    if iso2 is None:
        return ""
    return iso2_to_flag(iso2)
