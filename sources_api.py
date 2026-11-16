"""Compléments facultatifs : API officielles La bonne alternance et France Travail.

Elles republient beaucoup d'offres des grands groupes dont le site carrières n'est pas
lisible automatiquement (Thales, Safran, Orange, Capgemini...). Ignorées si les clés
d'API ne sont pas fournies.
"""
from __future__ import annotations

import datetime as dt
import re
import urllib.parse

import reseau
from filtres import normalise

LBA_SEARCH_URL = "https://api.apprentissage.beta.gouv.fr/api/job/v1/search"
LBA_HOME = "https://labonnealternance.apprentissage.beta.gouv.fr/"
FT_TOKEN_URL = ("https://entreprise.francetravail.fr/connexion/oauth2/access_token"
                "?realm=%2Fpartenaire")
FT_SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
FT_OFFER_PAGE = "https://candidat.francetravail.fr/offres/recherche/detail/{id}"
STATUTS_CLOS = {"filled", "cancelled", "pourvue", "annulee"}


def _lien(url: str | None) -> str | None:
    if url and urllib.parse.urlparse(url).scheme in ("http", "https"):
        return url
    return None


def la_bonne_alternance(cfg: dict, jeton: str) -> list[dict]:
    params: list[tuple[str, str]] = [("romes", ",".join(cfg["romes"]))]
    for dep in cfg.get("departements") or []:
        params.append(("departements", str(dep)))
    if cfg.get("niveau_diplome"):
        params.append(("target_diploma_level", str(cfg["niveau_diplome"])))
    _, corps = reseau.requete(LBA_SEARCH_URL + "?" + urllib.parse.urlencode(params),
                              entetes={"Authorization": f"Bearer {jeton}"})
    offres = []
    for job in (corps or {}).get("jobs") or []:
        offre = job.get("offer") or {}
        if normalise(str(offre.get("status") or "")) in STATUTS_CLOS:
            continue
        ident = job.get("identifier") or {}
        oid = ident.get("id") or ident.get("partner_job_id")
        if not oid:
            continue
        lieu = job.get("workplace") or {}
        partenaire = ident.get("partner_label")
        types = (job.get("contract") or {}).get("type") or []
        offres.append({
            "id": f"lba:{oid}",
            "type": "Alternance",
            "titre": (offre.get("title") or "").strip(),
            "entreprise": (lieu.get("brand") or lieu.get("name") or lieu.get("legal_name")
                           or "Entreprise non précisée").strip(),
            "lieu": ((lieu.get("location") or {}).get("address") or "").strip(),
            "pays": "FR",
            "contrat": " / ".join(types) if isinstance(types, list) else str(types),
            "description": (offre.get("description") or "")[:4000],
            "date": (offre.get("publication") or {}).get("creation") or "",
            "debut": (job.get("contract") or {}).get("start") or "",
            "url": _lien((job.get("apply") or {}).get("url")) or LBA_HOME,
            "source": f"La bonne alternance ({partenaire})" if partenaire else "La bonne alternance",
        })
    return offres


def _jeton_ft(client_id: str, secret: str) -> str:
    _, corps = reseau.requete(FT_TOKEN_URL, methode="POST", formulaire={
        "grant_type": "client_credentials", "client_id": client_id,
        "client_secret": secret, "scope": "api_offresdemploiv2 o2dsoffre"})
    return corps["access_token"]


def france_travail_cdi(cfg: dict, client_id: str, secret: str) -> list[dict]:
    jeton = _jeton_ft(client_id, secret)
    fin = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    debut = fin - dt.timedelta(days=int(cfg.get("jours", 3)))
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    deps = [str(d) for d in (cfg.get("departements") or [])] or [None]
    offres: dict[str, dict] = {}
    for mots in cfg["recherches"]:
        for dep in deps:
            params = {"motsCles": mots, "typeContrat": "CDI", "range": "0-149",
                      "minCreationDate": debut.strftime(fmt), "maxCreationDate": fin.strftime(fmt)}
            if dep:
                params["departement"] = dep
            _, corps = reseau.requete(FT_SEARCH_URL + "?" + urllib.parse.urlencode(params),
                                      entetes={"Authorization": f"Bearer {jeton}"})
            for r in (corps or {}).get("resultats") or []:
                if not r.get("id"):
                    continue
                exp = normalise(r.get("experienceLibelle"))
                m = re.search(r"(\d{1,2})\s*an", exp)
                offres[f"ft:{r['id']}"] = {
                    "id": f"ft:{r['id']}",
                    "type": "CDI",
                    "titre": (r.get("intitule") or "").strip(),
                    "entreprise": ((r.get("entreprise") or {}).get("nom") or "Entreprise non précisée").strip(),
                    "lieu": ((r.get("lieuTravail") or {}).get("libelle") or "").strip(),
                    "pays": "FR",
                    "contrat": r.get("typeContratLibelle") or "CDI",
                    "description": (r.get("description") or "")[:4000],
                    "date": r.get("dateCreation") or "",
                    "url": _lien((r.get("origineOffre") or {}).get("urlOrigine"))
                           or FT_OFFER_PAGE.format(id=urllib.parse.quote(str(r["id"]))),
                    "source": "France Travail",
                    "niveau_source": "junior" if r.get("experienceExige") == "D" else None,
                    "annees_source": int(m.group(1)) if m else (0 if r.get("experienceExige") == "D" else None),
                }
            reseau.pause(0.3)
    return list(offres.values())
