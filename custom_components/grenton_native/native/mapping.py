"""Best-effort proposal: a Grenton object → Home Assistant domain + read index.

This is only a *suggestion* for the object map — something the user reviews (and
will be able to override) before any entity is created. It is intentionally
simple and keyed on the object-id prefix (the 3-letter code after ``->``), which
is robust and always present. It is NOT authoritative.
"""

from __future__ import annotations

# prefix → (ha_domain, read_index, note)
_PREFIX_MAP: dict[str, tuple[str, int, str]] = {
    "DOU": ("light", 0, "DOUT — światło lub przekaźnik (switch)"),
    "DIM": ("light", 0, "ściemniacz (jasność)"),
    "LED": ("light", 0, "LED / RGB(W)"),
    "PWM": ("light", 0, "wyjście PWM"),
    "DAL": ("light", 0, "DALI — indeks stanu bywa inny (2)"),
    "ROL": ("cover", 0, "roleta — stan/pozycja"),
    "DIN": ("binary_sensor", 0, "wejście cyfrowe"),
    "SAT": ("binary_sensor", 0, "Satel"),
    "THE": ("climate", 0, "termostat — wiele cech"),
    "ONE": ("sensor", 0, "1-Wire"),
    "TEM": ("sensor", 0, "temperatura"),
    "ANA": ("sensor", 0, "wejście analogowe"),
    "ADC": ("sensor", 0, "ADC"),
    "SEN": ("sensor", 0, "czujnik"),
    "MOD": ("sensor", 0, "Modbus"),
    "VAL": ("sensor", 0, "wartość / zmienna"),
    "PRE": ("sensor", 0, "cecha PRE"),
    "PID": ("sensor", 0, "PID"),
    "FLA": ("switch", 0, "flaga"),
    "PAN": ("sensor", 0, "panel (czujnik / przycisk?)"),
    "ZWA": ("sensor", 0, "Z-Wave — zależnie od typu"),
}

_UNKNOWN = ("sensor", 0, "nieznany typ — sprawdź")


def propose(grenton_id: str, om_type: str | None = None) -> dict:
    """Return ``{"domain", "index", "note"}`` for an object id / grenton_id."""
    obj_id = grenton_id.split("->")[-1]
    prefix = obj_id[:3].upper()
    domain, index, note = _PREFIX_MAP.get(prefix, _UNKNOWN)
    return {"domain": domain, "index": index, "note": note}
