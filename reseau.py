"""Accès HTTP minimal (bibliothèque standard), facile à simuler dans les tests."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

USER_AGENT = "veille-alternance/2.0 (veille personnelle, 2 passages par jour)"
TIMEOUT = 30


def requete(url: str, *, methode: str = "GET", corps_json=None, formulaire: dict | None = None,
            entetes: dict | None = None) -> tuple[int, object]:
    """Renvoie (code HTTP, JSON décodé ou None). Lève urllib.error.HTTPError sur 4xx/5xx."""
    h = {"User-Agent": USER_AGENT, "Accept": "application/json",
         "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}
    data = None
    if corps_json is not None:
        data = json.dumps(corps_json).encode()
        h["Content-Type"] = "application/json"
    elif formulaire is not None:
        data = urllib.parse.urlencode(formulaire).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    h.update(entetes or {})
    req = urllib.request.Request(url, data=data, headers=h, method=methode)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        brut = resp.read()
        if resp.status == 204 or not brut:
            return resp.status, None
        return resp.status, json.loads(brut.decode("utf-8"))


def pause(secondes: float) -> None:
    """Délai de politesse entre deux requêtes vers un même site."""
    time.sleep(secondes)
