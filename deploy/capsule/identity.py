#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Client-Identitaet abstrahiert — Python-Spiegel (Issue #992).

Duenne Spiegelung der C++-Logik in ``tools/gns-proxy/player_identity.h`` fuer
die Python-Schnittstelle (``deploy/capsule/``). Kein Win32/GNS — reine stdlib,
host-testbar (siehe ``test_identity.py``; identische Testfall-Tabelle wie der
C++-Test ``test_player_identity.cpp``).

Herkunft/Format wie im Relay:

    steamid:<id>  -> Kind.STEAM   (Steam-Client)
    str:<hex>     -> Kind.GENERIC (anonym, stabil pro Installation — z. B. GOG)
    account:<id>  -> Kind.ACCOUNT (reservierter Andockpunkt, KEINE Account-Logik)

``authorized`` ist passiv: verbunden + gueltige Identitaet ⇒ der Relay darf
routen. ``canonical`` ist fuer alle Kinds byte-gleich zu ``raw`` — nur Hex wird
im Generic-Rest lowercase normalisiert, damit der heutige Routen-/Pin-/Claim-Key
(``str:…`` / ``steamid:…``) unveraendert bleibt.
"""

from __future__ import annotations

import enum
import dataclasses
from typing import Optional


class Kind(enum.Enum):
    """Art der Client-Identitaet."""

    STEAM = "steam"
    GENERIC = "generic"
    ACCOUNT = "account"


@dataclasses.dataclass
class PlayerIdentity:
    """Zerlegte Identitaet. ``canonical`` ist der Routen-/Pin-/Claim-Key."""

    kind: Kind
    raw: str
    canonical: str
    valid: bool


def _lower_hex(text: str) -> str:
    """Nur ASCII-Hex-Buchstaben A–F lowercase (Ziffern/andere Zeichen bleiben)."""
    out = []
    for ch in text:
        if "A" <= ch <= "F":
            out.append(chr(ord(ch) - ord("A") + ord("a")))
        else:
            out.append(ch)
    return "".join(out)


def is_identity_like(raw: str) -> bool:
    """Erkennt die bekannten Identitaets-Praefixe (Spielnamen -> False)."""
    return (
        raw.startswith("steamid:")
        or raw.startswith("str:")
        or raw.startswith("account:")
    )


def parse_identity(raw: str) -> PlayerIdentity:
    """Zerlegt ``raw`` in eine :class:`PlayerIdentity`.

    Tolerant: nur Praefix + nicht-leerer Rest werden geprueft, der Rest wird
    NICHT inhaltlich validiert (unbekannte Formen -> ``valid=False``).
    """
    if raw.startswith("steamid:") and len(raw) > 8:
        return PlayerIdentity(Kind.STEAM, raw, raw, True)
    if raw.startswith("str:") and len(raw) > 4:
        return PlayerIdentity(Kind.GENERIC, raw, "str:" + _lower_hex(raw[4:]), True)
    if raw.startswith("account:") and len(raw) > 8:
        return PlayerIdentity(Kind.ACCOUNT, raw, raw, True)
    return PlayerIdentity(Kind.GENERIC, raw, raw, False)


def canonicalize(raw: Optional[str]) -> Optional[str]:
    """Kanonische Form fuer die Schnittstelle; ``None`` bleibt ``None``.

    Ungueltige/unbekannte Formen werden unveraendert durchgereicht (der Relay
    faellt auf den rohen String zurueck), damit kein Wert stillschweigend
    verloren geht.
    """
    if raw is None:
        return None
    parsed = parse_identity(str(raw))
    return parsed.canonical if parsed.valid else str(raw)


def is_authorized(connected: bool, identity: PlayerIdentity) -> bool:
    """Passiv: verbunden + gueltige Identitaet (kein Login, keine Zusatzbestaetigung)."""
    return bool(connected) and bool(identity.valid)