"""Lecture d'un site carrières via navigateur sans écran (Playwright), pour les sites
sans API propre : SuccessFactors, iCIMS, Talentsoft, Oracle, sites maison.

Deux stratégies, dans l'ordre :

1. JSON-LD (par défaut, aucun réglage par site) : la plupart des sites carrières
   publient des blocs <script type="application/ld+json"> de type « JobPosting »
   pour Google for Jobs. On les lit directement : titre, lieu, date, lien, contrat.

2. Sélecteurs CSS (repli) : si le site n'a pas de JSON-LD, on fournit par entreprise
   un bloc "dom" {carte, titre, lien, lieu} dans config.json.

Chaque offre renvoyée a le même format que les connecteurs ATS (voir sources_ats).
Playwright n'est importé qu'au moment où on s'en sert : les autres sources (Lever,
Greenhouse…) continuent de fonctionner même si Playwright n'est pas installé.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import urllib.parse

from filtres import texte_brut

SOURCE = "Site carrières (navigateur)"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/121.0.0.0 Safari/537.36")

# Extrait tout le contenu des balises <script type="application/ld+json">.
_BLOCS_JSONLD = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)

# Extraction DOM exécutée DANS la page (sélecteurs fournis par l'utilisateur).
_JS_DOM = """(spec) => Array.from(document.querySelectorAll(spec.carte)).map(c => {
  const lien = spec.lien ? c.querySelector(spec.lien) : (c.matches('a') ? c : c.querySelector('a'));
  const el = s => s ? c.querySelector(s) : null;
  const txt = n => (n && n.textContent ? n.textContent : '').replace(/\\s+/g, ' ').trim();
  return {
    titre: txt(spec.titre ? el(spec.titre) : lien),
    url: lien ? lien.href : '',
    lieu: txt(el(spec.lieu)),
  };
})"""


# --------------------------------------------------------------------- outils

def _lien(url, base: str = "") -> str | None:
    """Rend une URL absolue et n'accepte que http(s) (bloque javascript:, data:…)."""
    if not url:
        return None
    if base:
        url = urllib.parse.urljoin(base, url)
    return url if urllib.parse.urlparse(url).scheme in ("http", "https") else None


def _id_stable(entreprise: str, titre: str, url: str) -> str:
    graine = f"{entreprise}|{titre}|{url}".encode("utf-8")
    return "nav:" + hashlib.sha1(graine).hexdigest()[:16]


def _base_offre(entreprise: str, titre: str, lieu: str, url: str, **extra) -> dict:
    o = dict(id=_id_stable(entreprise, titre, url or ""), titre=(titre or "").strip(),
             entreprise=entreprise, lieu=(lieu or "").strip(), lieu_detail="", pays="",
             contrat="", description="", date="", url=url or "", source=SOURCE,
             niveau_source=None, annees_source=None)
    o.update(extra)
    return o


# --------------------------------------------------------------------- JSON-LD

def _lieu_jsonld(jl) -> tuple[str, str]:
    if isinstance(jl, list):
        jl = jl[0] if jl else {}
    if not isinstance(jl, dict):
        return "", ""
    addr = jl.get("address") or {}
    if isinstance(addr, list):
        addr = addr[0] if addr else {}
    if isinstance(addr, str):
        return addr, ""
    if not isinstance(addr, dict):
        return "", ""
    pays = addr.get("addressCountry") or ""
    if isinstance(pays, dict):
        pays = pays.get("name") or pays.get("alternateName") or ""
    lieu = ", ".join(x for x in [addr.get("addressLocality"), addr.get("addressRegion")] if x)
    return lieu, str(pays)


def _url_jsonld(obj: dict, base: str) -> str:
    mep = obj.get("mainEntityOfPage")
    if isinstance(mep, dict):
        mep = mep.get("@id")
    for candidat in (obj.get("url"), mep, obj.get("@id")):
        lien = _lien(candidat, base)
        if lien:
            return lien
    return base


def _iter_jobposting(data):
    """Parcourt un document JSON-LD et rend chaque objet de type JobPosting."""
    if isinstance(data, list):
        for d in data:
            yield from _iter_jobposting(d)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _iter_jobposting(data["@graph"])
        t = data.get("@type")
        types = t if isinstance(t, list) else [t]
        if "JobPosting" in types:
            yield data


def offres_depuis_jsonld(html: str, entreprise: str, base: str = "") -> list[dict]:
    """Extrait les JobPosting d'une page. Fonction pure (testable sans navigateur)."""
    offres, vus = [], set()
    for bloc in _BLOCS_JSONLD.findall(html or ""):
        try:
            data = json.loads(bloc.strip())
        except ValueError:
            continue  # un bloc illisible ne casse pas les autres
        for obj in _iter_jobposting(data):
            titre = (obj.get("title") or "").strip()
            if not titre:
                continue
            url = _url_jsonld(obj, base)
            cle = (titre, url)
            if cle in vus:
                continue
            vus.add(cle)
            lieu, pays = _lieu_jsonld(obj.get("jobLocation"))
            org = obj.get("hiringOrganization")
            nom = org.get("name") if isinstance(org, dict) and org.get("name") else entreprise
            emp = obj.get("employmentType")
            contrat = ", ".join(emp) if isinstance(emp, list) else (emp or "")
            offres.append(_base_offre(
                nom, titre, lieu, url, pays=pays, contrat=contrat,
                date=(obj.get("datePosted") or "")[:25],
                description=texte_brut(obj.get("description"))[:4000]))
    return offres


# --------------------------------------------------------------- navigateur

def _ouvre(url: str, spec: dict | None, timeout_ms: int, defilements: int) -> tuple[str, list]:
    """Charge la page dans Chromium sans écran. Renvoie (html, lignes DOM éventuelles)."""
    from playwright.sync_api import sync_playwright  # importé seulement si on l'utilise

    rows: list = []
    with sync_playwright() as p:
        nav = p.chromium.launch(args=["--no-sandbox"])
        try:
            ctx = nav.new_context(locale="fr-FR", user_agent=UA,
                                  viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except Exception:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if spec and spec.get("attendre"):
                try:
                    page.wait_for_selector(spec["attendre"], timeout=timeout_ms)
                except Exception:
                    pass  # on prend la page telle quelle
            for _ in range(defilements):  # charge les offres ajoutées au défilement
                page.mouse.wheel(0, 24000)
                page.wait_for_timeout(700)
            html = page.content()
            if spec and spec.get("carte"):
                try:
                    rows = page.evaluate(_JS_DOM, spec)
                except Exception:
                    rows = []
        finally:
            nav.close()
    return html, rows


def collecte(e: dict, pre=None, timeout_ms: int = 30000, defilements: int = 3) -> list[dict]:
    """Un site carrières lu au navigateur. `pre` (pré-filtre par titre) est accepté
    pour homogénéité avec les connecteurs ATS, mais le tri fin reste fait par Profil.classe."""
    url = e["url"]
    spec = e.get("dom")
    html, rows = _ouvre(url, spec, timeout_ms, defilements)
    offres = offres_depuis_jsonld(html, e["nom"], url)
    if not offres:  # pas de JSON-LD : on retombe sur les sélecteurs CSS
        for r in rows:
            titre = (r.get("titre") or "").strip()
            if titre:
                offres.append(_base_offre(e["nom"], titre, r.get("lieu") or "",
                                          _lien(r.get("url"), url) or url))
    return offres
