"""Offres lues directement sur les sites carrières, via le logiciel de recrutement (ATS)
qui les héberge. Chaque connecteur renvoie des offres au même format :

  id, titre, entreprise, lieu, lieu_detail, pays, contrat, description, date, url, source,
  niveau_source ("junior" / "senior" / None), annees_source (int ou None)

  - Lever, Greenhouse, SmartRecruiters, Recruitee : API publiques et documentées.
  - Workday : point d'accès JSON utilisé par la page carrière elle-même ; on l'interroge
    avec des recherches ciblées, une pause d'une seconde entre deux appels.
"""
from __future__ import annotations

import datetime as dt
import re
import urllib.parse
from typing import Callable

import reseau
from filtres import normalise, texte_brut

Offre = dict
PreFiltre = Callable[[str], bool]   # titre -> vaut-il la peine de lire le détail ?


def _iso_ms(ms) -> str:
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return ""


def _offre(**champs) -> Offre:
    base = dict(id="", titre="", entreprise="", lieu="", lieu_detail="", pays="", contrat="",
                description="", date="", url="", source="", niveau_source=None, annees_source=None)
    base.update({k: v for k, v in champs.items() if v is not None})
    base["titre"] = (base["titre"] or "").strip()
    base["description"] = (base["description"] or "")[:4000]
    return base


# --------------------------------------------------------------------- Lever

def lever(e: dict, pre: PreFiltre) -> list[Offre]:
    hote = "api.eu.lever.co" if e.get("region") == "eu" else "api.lever.co"
    _, data = reseau.requete(f"https://{hote}/v0/postings/{urllib.parse.quote(e['id'])}?mode=json")
    offres = []
    for p in data or []:
        cat = p.get("categories") or {}
        listes = " ".join(texte_brut(l.get("content")) for l in p.get("lists") or [])
        offres.append(_offre(
            id=f"lever:{e['id']}:{p.get('id')}",
            titre=p.get("text"),
            entreprise=e["nom"],
            lieu=cat.get("location") or "",
            lieu_detail=" ".join(cat.get("allLocations") or []),
            pays=p.get("country") or "",
            contrat=cat.get("commitment") or "",
            description=" ".join(filter(None, [p.get("descriptionPlain"), listes, p.get("additionalPlain")])),
            date=_iso_ms(p.get("createdAt")),
            url=p.get("hostedUrl"),
            source="Site carrières (Lever)",
        ))
    return offres


# ---------------------------------------------------------------- Greenhouse

def greenhouse(e: dict, pre: PreFiltre) -> list[Offre]:
    hote = "boards-api.eu.greenhouse.io" if e.get("region") == "eu" else "boards-api.greenhouse.io"
    _, data = reseau.requete(f"https://{hote}/v1/boards/{urllib.parse.quote(e['id'])}/jobs?content=true")
    offres = []
    for j in (data or {}).get("jobs") or []:
        bureaux = " ".join(
            f"{b.get('name') or ''} {b.get('location') or ''}" for b in j.get("offices") or [])
        offres.append(_offre(
            id=f"gh:{e['id']}:{j.get('id')}",
            titre=j.get("title"),
            entreprise=e["nom"],
            lieu=(j.get("location") or {}).get("name") or "",
            lieu_detail=bureaux,
            description=texte_brut(j.get("content")),
            date=j.get("first_published") or j.get("updated_at") or "",
            url=j.get("absolute_url"),
            source="Site carrières (Greenhouse)",
        ))
    return offres


# ----------------------------------------------------------- SmartRecruiters

_SR_JUNIOR = {"entry_level", "internship"}
_SR_SENIOR = {"mid_senior_level", "director", "executive"}


def smartrecruiters(e: dict, pre: PreFiltre, max_details: int = 40) -> list[Offre]:
    base = f"https://api.smartrecruiters.com/v1/companies/{urllib.parse.quote(e['id'])}/postings"
    offres, offset, details = [], 0, 0
    while True:
        params = {"limit": 100, "offset": offset}
        if e.get("pays", "fr"):
            params["country"] = e.get("pays", "fr")
        _, data = reseau.requete(base + "?" + urllib.parse.urlencode(params))
        contenu = (data or {}).get("content") or []
        for p in contenu:
            titre = p.get("name") or ""
            if not pre(titre):
                continue
            loc = p.get("location") or {}
            niveau = (p.get("experienceLevel") or {}).get("id")
            o = _offre(
                id=f"sr:{e['id']}:{p.get('id')}",
                titre=titre,
                entreprise=e["nom"],
                lieu=", ".join(filter(None, [loc.get("city"), loc.get("region")])),
                pays=loc.get("country") or "",
                contrat=(p.get("typeOfEmployment") or {}).get("label") or "",
                date=p.get("releasedDate") or "",
                url=f"https://jobs.smartrecruiters.com/{e['id']}/{p.get('id')}",
                source="Site carrières (SmartRecruiters)",
                niveau_source="junior" if niveau in _SR_JUNIOR else "senior" if niveau in _SR_SENIOR else None,
            )
            if details < max_details:
                details += 1
                reseau.pause(0.5)
                try:
                    _, d = reseau.requete(f"{base}/{urllib.parse.quote(str(p.get('id')))}")
                    sections = ((d or {}).get("jobAd") or {}).get("sections") or {}
                    o["description"] = texte_brut(" ".join(
                        (sections.get(k) or {}).get("text") or "" for k in ("jobDescription", "qualifications")))[:4000]
                except Exception:
                    pass  # on garde l'offre sans description
            offres.append(o)
        offset += len(contenu)
        if not contenu or offset >= int((data or {}).get("totalFound") or 0) or offset >= 1000:
            break
        reseau.pause(0.5)
    return offres


# ----------------------------------------------------------------- Recruitee

_RC_JUNIOR = {"entry_level", "student_graduate", "student_college", "graduate"}
_RC_SENIOR = {"senior_level", "experienced", "executive", "manager", "director"}


def recruitee(e: dict, pre: PreFiltre) -> list[Offre]:
    _, data = reseau.requete(f"https://{urllib.parse.quote(e['id'])}.recruitee.com/api/offers/")
    offres = []
    for p in (data or {}).get("offers") or []:
        niveau = (p.get("experience_code") or "").lower()
        offres.append(_offre(
            id=f"rc:{e['id']}:{p.get('id')}",
            titre=p.get("title"),
            entreprise=e["nom"],
            lieu=p.get("location") or p.get("city") or "",
            pays=p.get("country_code") or "",
            contrat=(p.get("employment_type_code") or "").replace("_", " "),
            description=texte_brut(f"{p.get('description') or ''} {p.get('requirements') or ''}"),
            date=p.get("published_at") or p.get("created_at") or "",
            url=p.get("careers_url"),
            source="Site carrières (Recruitee)",
            niveau_source="junior" if niveau in _RC_JUNIOR else "senior" if niveau in _RC_SENIOR else None,
        ))
    return offres


# ------------------------------------------------------------------- Workday

_PLUSIEURS_LIEUX = re.compile(r"^\d+\s+(locations|emplacements|lieux|sites|standorte)$")


def _date_workday(texte: str, maintenant: dt.datetime) -> str:
    t = normalise(texte)
    jours = None
    if "today" in t or "aujourd" in t:
        jours = 0
    elif "yesterday" in t or "hier" in t:
        jours = 1
    else:
        m = re.search(r"(\d+)\+?\s*(days|day|jours|jour)", t)
        if m:
            jours = int(m.group(1))
    if jours is None:
        return ""
    return (maintenant - dt.timedelta(days=jours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_workday(url: str) -> dict:
    """https://ag.wd3.myworkdayjobs.com/fr-FR/Airbus -> hôte, tenant, site."""
    u = urllib.parse.urlparse(url)
    morceaux = [m for m in u.path.split("/") if m]
    if morceaux and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", morceaux[0]):
        morceaux = morceaux[1:]
    if not u.netloc.endswith("myworkdayjobs.com") or not morceaux:
        raise ValueError(f"URL Workday non reconnue : {url}")
    return {"hote": u.netloc, "tenant": u.netloc.split(".")[0], "site": morceaux[0]}


def workday(e: dict, pre: PreFiltre, recherches: list[str], lieu_ok: Callable[[str], bool],
            max_pages: int = 3, max_details: int = 40) -> list[Offre]:
    w = parse_workday(e["url"])
    api = f"https://{w['hote']}/wday/cxs/{w['tenant']}/{w['site']}"
    maintenant = dt.datetime.now(dt.timezone.utc)
    retenus: dict[str, dict] = {}
    for q in recherches:
        for page in range(max_pages):
            reseau.pause(1.0)
            _, data = reseau.requete(api + "/jobs", methode="POST", corps_json={
                "appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": q})
            postes = (data or {}).get("jobPostings") or []
            for p in postes:
                chemin = p.get("externalPath") or ""
                lieux = p.get("locationsText") or ""
                if not chemin or chemin in retenus or not pre(p.get("title") or ""):
                    continue
                if not (lieu_ok(lieux) or _PLUSIEURS_LIEUX.match(normalise(lieux))):
                    continue
                retenus[chemin] = p
            if len(postes) < 20 or (page + 1) * 20 >= int((data or {}).get("total") or 0):
                break

    offres = []
    for i, (chemin, p) in enumerate(retenus.items()):
        o = _offre(
            id=f"wd:{w['tenant']}:{chemin}",
            titre=p.get("title"),
            entreprise=e["nom"],
            lieu=p.get("locationsText") or "",
            date=_date_workday(p.get("postedOn") or "", maintenant),
            url=f"https://{w['hote']}/{w['site']}{chemin}",
            source="Site carrières (Workday)",
        )
        if i < max_details:
            reseau.pause(1.0)
            try:
                _, d = reseau.requete(api + chemin)
                info = (d or {}).get("jobPostingInfo") or {}
                o["description"] = texte_brut(info.get("jobDescription"))[:4000]
                o["lieu"] = info.get("location") or o["lieu"]
                o["lieu_detail"] = " ".join(info.get("additionalLocations") or [])
                o["pays"] = ((info.get("country") or {}).get("descriptor")) or ""
                o["contrat"] = info.get("timeType") or ""
                o["url"] = info.get("externalUrl") or o["url"]
            except Exception:
                pass
        offres.append(o)
    return offres


# ------------------------------------------------------- ajout par URL

def detecte_ats(url: str, nom: str | None = None) -> dict:
    """URL d'une page carrière -> entrée prête pour config.json."""
    u = urllib.parse.urlparse(url.strip())
    hote, chemin = u.netloc.lower(), [m for m in u.path.split("/") if m]
    entree: dict
    if hote.endswith("myworkdayjobs.com"):
        w = parse_workday(url)
        entree = {"ats": "workday", "url": f"https://{w['hote']}/{w['site']}"}
        nom = nom or w["site"]
    elif hote in ("jobs.lever.co", "jobs.eu.lever.co") and chemin:
        entree = {"ats": "lever", "id": chemin[0]}
        if ".eu." in hote:
            entree["region"] = "eu"
    elif ("greenhouse.io" in hote) and chemin:
        jeton = chemin[0] if chemin[0] != "embed" else urllib.parse.parse_qs(u.query).get("for", [""])[0]
        entree = {"ats": "greenhouse", "id": jeton}
        if ".eu." in hote:
            entree["region"] = "eu"
    elif hote in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and chemin:
        entree = {"ats": "smartrecruiters", "id": chemin[0]}
    elif hote.endswith(".recruitee.com"):
        entree = {"ats": "recruitee", "id": hote.split(".")[0]}
    else:
        raise ValueError(
            "ATS non reconnu. Ouvre une offre de l'entreprise : si son adresse contient "
            "myworkdayjobs.com, lever.co, greenhouse.io, smartrecruiters.com ou recruitee.com, "
            "colle cette adresse-là.")
    return {"nom": nom or entree.get("id", ""), **entree}
