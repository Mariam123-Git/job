#!/usr/bin/env python3
"""Veille d'offres : alternance (cybersécurité, DevOps, DevSecOps) et CDI junior
(DevOps, Cloud, DevSecOps, Cloud Security, Platform, SRE, CloudOps, infra/systèmes, CI/CD).

Les offres sont lues directement sur les sites carrières des entreprises listées dans
config.json (Workday, Lever, Greenhouse, SmartRecruiters, Recruitee). Les API publiques
La bonne alternance et France Travail servent de complément si leurs clés sont fournies.

Usage :
  python veille.py --previous public/offers.json --out public
  python veille.py --ajouter "https://jobs.lever.co/entreprise" --nom "Entreprise"

Variables d'environnement (toutes facultatives) :
  LBA_API_TOKEN, FT_CLIENT_ID, FT_CLIENT_SECRET          compléments API
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, MAIL_TO   email des nouveautés
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import smtplib
import sys
import urllib.error
from email.message import EmailMessage
from pathlib import Path

import reseau
import sources_api
import sources_ats
from filtres import Profil, normalise

ICI = Path(__file__).resolve().parent


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------- collecte

def collecte_entreprise(e: dict, profil: Profil, cfg: dict) -> list[dict]:
    ats = e.get("ats")
    pre = profil.titre_interessant
    if ats == "lever":
        return sources_ats.lever(e, pre)
    if ats == "greenhouse":
        return sources_ats.greenhouse(e, pre)
    if ats == "smartrecruiters":
        return sources_ats.smartrecruiters(e, pre)
    if ats == "recruitee":
        return sources_ats.recruitee(e, pre)
    if ats == "workday":
        return sources_ats.workday(e, pre, cfg.get("workday_recherches") or ["alternance"],
                                   profil.lieu_texte_ok)
    if ats == "navigateur":
        import sources_navigateur  # import tardif : Playwright n'est requis que pour ce mode
        return sources_navigateur.collecte(e)
    raise ValueError(f"ATS inconnu : {ats!r}")


def collecte(cfg: dict, profil: Profil) -> tuple[list[dict], list[str], dict]:
    offres, alertes, bilan = [], [], {}
    for e in cfg.get("entreprises") or []:
        if e.get("actif") is False:
            continue
        nom = e.get("nom") or e.get("id") or "?"
        try:
            brutes = collecte_entreprise(e, profil, cfg)
            gardees = [o for o in brutes if profil.classe(o)]
            bilan[nom] = (len(brutes), len(gardees))
            offres += gardees
            print(f"[ok] {nom} ({e.get('ats')}) : {len(brutes)} offres lues, {len(gardees)} retenues")
        except Exception as ex:  # une entreprise en panne ne bloque pas les autres
            alertes.append(f"{nom} ({e.get('ats')}) : {ex}")
            bilan[nom] = None

    api = cfg.get("complements_api") or {}
    jeton = os.environ.get("LBA_API_TOKEN")
    if jeton and api.get("alternance_la_bonne_alternance"):
        try:
            brutes = sources_api.la_bonne_alternance(api["alternance_la_bonne_alternance"], jeton)
            gardees = [o for o in brutes if profil.classe(o)]
            bilan["La bonne alternance (API)"] = (len(brutes), len(gardees))
            offres += gardees
        except Exception as ex:
            alertes.append(f"La bonne alternance (API) : {ex}")
    cid, sec = os.environ.get("FT_CLIENT_ID"), os.environ.get("FT_CLIENT_SECRET")
    if cid and sec and api.get("cdi_france_travail"):
        try:
            brutes = sources_api.france_travail_cdi(api["cdi_france_travail"], cid, sec)
            gardees = [o for o in brutes if profil.classe(o)]
            bilan["France Travail (API)"] = (len(brutes), len(gardees))
            offres += gardees
        except Exception as ex:
            alertes.append(f"France Travail (API) : {ex}")
    return dedoublonne(offres), alertes, bilan


def dedoublonne(offres: list[dict]) -> list[dict]:
    """Même id, ou même titre chez la même entreprise : on garde le site carrières."""
    rang = lambda o: 0 if o.get("source", "").startswith("Site carrières") else 1  # noqa: E731
    vues: dict[str, dict] = {}
    for o in sorted(offres, key=rang):
        cle = normalise(o["titre"]) + "|" + normalise(o["entreprise"])
        if o["id"] in {v["id"] for v in vues.values()} or cle in vues:
            continue
        vues[cle] = o
    return list(vues.values())


# ----------------------------------------------------------------- historique

def charge_precedent(source: str | None) -> list[dict]:
    if not source:
        return []
    try:
        if source.startswith(("http://", "https://")):
            entetes = {}
            api = os.environ.get("CI_API_V4_URL")
            if api and source.startswith(api) and os.environ.get("CI_JOB_TOKEN"):
                entetes["JOB-TOKEN"] = os.environ["CI_JOB_TOKEN"]  # artefacts GitLab
            _, corps = reseau.requete(source, entetes=entetes)
        else:
            p = Path(source)
            corps = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"[info] pas de relevé précédent lisible ({e}) : premier passage.")
        return []
    return (corps or {}).get("offres") or []


def fusionne(anciennes: list[dict], nouvelles: list[dict], quand: dt.datetime,
             conserver_jours: int) -> tuple[list[dict], list[dict]]:
    """Renvoie (offres conservées, offres jamais vues auparavant)."""
    par_id = {o["id"]: o for o in anciennes}
    deja_vues = set(par_id)
    for o in nouvelles:
        o["premiere_vue"] = par_id.get(o["id"], {}).get("premiere_vue", iso(quand))
        o["derniere_vue"] = iso(quand)
        par_id[o["id"]] = o
    limite = iso(quand - dt.timedelta(days=conserver_jours))
    gardees = [o for o in par_id.values() if o.get("derniere_vue", "") >= limite]
    gardees.sort(key=lambda o: (o.get("premiere_vue", ""), o.get("date", "")), reverse=True)
    ajoutees = [o for o in nouvelles if o["id"] not in deja_vues]
    return gardees, ajoutees


# --------------------------------------------------------------------- sorties

def date_fr(valeur: str) -> str:
    try:
        return dt.datetime.fromisoformat((valeur or "").replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except ValueError:
        return ""


def heure_paris(quand: dt.datetime) -> str:
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Paris")
    except Exception:
        tz = dt.timezone(dt.timedelta(hours=1))
    return quand.astimezone(tz).strftime("%d/%m/%Y à %H:%M")


def page_html(offres: list[dict], nouvelles_ids: set[str], quand: dt.datetime,
              alertes: list[str], bilan: dict) -> str:
    e = html.escape
    cartes = []
    for o in offres:
        nouveau = o["id"] in nouvelles_ids
        junior = o.get("niveau") == "Junior"
        badges = [f'<span class="tag {"alt" if o["type"] == "Alternance" else "cdi"}">{e(o["type"])}</span>']
        if nouveau:
            badges.append('<span class="tag new">Nouveau</span>')
        if o.get("niveau"):
            badges.append(f'<span class="tag {"jun" if junior else "nsp"}">{e(o["niveau"])}</span>')
        filtre_txt = normalise(f"{o['titre']} {o['entreprise']} {o.get('lieu', '')}")
        cartes.append(
            f'<article class="offre" data-type="{e(o["type"])}" data-nouveau="{int(nouveau)}" '
            f'data-junior="{int(junior)}" data-texte="{e(filtre_txt)}">'
            f'<div class="tete">{"".join(badges)}'
            f'<span class="date">{("publiée le " + e(date_fr(o.get("date", "")))) if date_fr(o.get("date", "")) else ""}</span></div>'
            f'<h2><a href="{e(o["url"])}" target="_blank" rel="noopener noreferrer">{e(o["titre"] or "Sans titre")}</a></h2>'
            f'<p class="meta"><strong>{e(o["entreprise"])}</strong> · {e(o.get("lieu") or "Lieu non précisé")}</p>'
            f'<p class="meta petit">{e(" · ".join(filter(None, [o.get("contrat"), o.get("source")])))}</p></article>'
        )
    nb_alt = sum(o["type"] == "Alternance" for o in offres)
    nb_cdi = len(offres) - nb_alt
    lignes_bilan = "".join(
        f"<li>{e(nom)} : " + ("<em>erreur</em>" if v is None else f"{v[0]} lues, {v[1]} retenues") + "</li>"
        for nom, v in bilan.items())
    bloc_alertes = ""
    if alertes:
        bloc_alertes = ('<details class="alerte"><summary>' + f"{len(alertes)} source(s) en erreur"
                        + "</summary><ul>" + "".join(f"<li>{e(a)}</li>" for a in alertes) + "</ul></details>")
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Veille alternance et CDI</title>
<style>
:root {{ --bg:#f6f7f9; --carte:#fff; --texte:#1c2430; --doux:#5b6675; --ligne:#e1e5ea; --accent:#1f4e79;
  --sur-accent:#fff; --alt:#e6f0fa; --cdi:#eef6ea; --new:#fff1d6; --jun:#e9e4fb; --nsp:#eceff2; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#11151a; --carte:#1a2028; --texte:#e7ebf0; --doux:#9aa5b3;
  --ligne:#2a323d; --accent:#7fb2e5; --sur-accent:#0b1520; --alt:#1d3247; --cdi:#21331d; --new:#4a3a17;
  --jun:#33294f; --nsp:#262c34; }} }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--texte); font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif }}
main {{ max-width:880px; margin:0 auto; padding:24px 16px 48px }}
h1 {{ font-size:22px; margin:0 0 4px }}
.sous {{ color:var(--doux); margin:0 0 14px }}
.barre {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px; position:sticky; top:0; background:var(--bg); padding:8px 0; z-index:1 }}
.barre button {{ border:1px solid var(--ligne); background:var(--carte); color:var(--texte); border-radius:999px; padding:6px 12px; cursor:pointer; font:inherit }}
.barre button[aria-pressed=true] {{ background:var(--accent); border-color:var(--accent); color:var(--sur-accent) }}
.barre input {{ flex:1 1 200px; min-width:0; border:1px solid var(--ligne); background:var(--carte); color:var(--texte); border-radius:8px; padding:6px 10px; font:inherit }}
.offre {{ background:var(--carte); border:1px solid var(--ligne); border-radius:10px; padding:12px 14px; margin-bottom:10px }}
.offre h2 {{ font-size:16px; margin:6px 0 2px; overflow-wrap:anywhere }}
.offre a {{ color:var(--accent); text-decoration:none }} .offre a:hover {{ text-decoration:underline }}
.tete {{ display:flex; gap:6px; align-items:center; flex-wrap:wrap }}
.tag {{ font-size:12px; border-radius:6px; padding:1px 7px; font-weight:600 }}
.tag.alt {{ background:var(--alt) }} .tag.cdi {{ background:var(--cdi) }} .tag.new {{ background:var(--new) }}
.tag.jun {{ background:var(--jun) }} .tag.nsp {{ background:var(--nsp); font-weight:500 }}
.date {{ color:var(--doux); font-size:12px; margin-left:auto }}
.meta {{ margin:2px 0; color:var(--doux) }} .meta strong {{ color:var(--texte) }} .petit {{ font-size:13px }}
details {{ background:var(--carte); border:1px solid var(--ligne); border-radius:8px; padding:8px 12px; margin:0 0 12px; color:var(--doux) }}
details.alerte {{ background:var(--new); color:var(--texte) }}
details ul {{ margin:6px 0 0; padding-left:18px }}
.vide {{ color:var(--doux); text-align:center; padding:32px 0 }}
</style></head><body><main>
<h1>Veille alternance et CDI junior</h1>
<p class="sous">Mise à jour le {e(heure_paris(quand))} · {len(offres)} offres ({nb_alt} alternance, {nb_cdi} CDI) · {len(nouvelles_ids)} nouvelle(s)</p>
{bloc_alertes}
<details><summary>Sources interrogées ({len(bilan)})</summary><ul>{lignes_bilan}</ul></details>
<div class="barre">
<button data-f="tous" aria-pressed="true">Toutes</button>
<button data-f="nouveau" aria-pressed="false">Nouvelles</button>
<button data-f="Alternance" aria-pressed="false">Alternance</button>
<button data-f="CDI" aria-pressed="false">CDI</button>
<button data-f="junior" aria-pressed="false">CDI junior</button>
<input type="search" placeholder="Filtrer : ville, entreprise, mot-clé…" aria-label="Filtrer">
</div>
<section id="liste">{"".join(cartes) or '<p class="vide">Aucune offre pour le moment.</p>'}</section>
<p class="vide" id="rien" hidden>Aucune offre ne correspond à ce filtre.</p>
</main>
<script>
const boutons = document.querySelectorAll('.barre button');
const champ = document.querySelector('.barre input');
let filtre = 'tous';
const plat = s => s.normalize('NFKD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase();
function garde(el) {{
  if (filtre === 'nouveau') return el.dataset.nouveau === '1';
  if (filtre === 'junior') return el.dataset.junior === '1';
  return filtre === 'tous' || el.dataset.type === filtre;
}}
function applique() {{
  const q = plat(champ.value.trim());
  let n = 0;
  document.querySelectorAll('.offre').forEach(el => {{
    const ok = garde(el) && (!q || el.dataset.texte.includes(q));
    el.hidden = !ok; if (ok) n++;
  }});
  document.getElementById('rien').hidden = n > 0 || !document.querySelector('.offre');
}}
boutons.forEach(b => b.addEventListener('click', () => {{
  filtre = b.dataset.f; boutons.forEach(x => x.setAttribute('aria-pressed', x === b)); applique();
}}));
champ.addEventListener('input', applique);
</script></body></html>
"""


def envoie_email(nouvelles: list[dict], lien_page: str | None) -> None:
    hote, dest = os.environ.get("SMTP_HOST"), os.environ.get("MAIL_TO")
    if not (hote and dest and nouvelles):
        return
    port = int(os.environ.get("SMTP_PORT", "465"))
    user, mdp = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", "")
    nb_alt = sum(o["type"] == "Alternance" for o in nouvelles)
    msg = EmailMessage()
    msg["Subject"] = f"Veille : {len(nouvelles)} nouvelle(s) offre(s) ({nb_alt} alternance, {len(nouvelles) - nb_alt} CDI)"
    msg["From"], msg["To"] = user or dest, dest
    lignes = [f"- [{o['type']}{' junior' if o.get('niveau') == 'Junior' else ''}] {o['titre']} - "
              f"{o['entreprise']} ({o.get('lieu', '')})\n  {o['url']}" for o in nouvelles[:40]]
    if len(nouvelles) > 40:
        lignes.append(f"... et {len(nouvelles) - 40} autres.")
    if lien_page:
        lignes.append(f"\nToutes les offres : {lien_page}")
    msg.set_content("\n".join(lignes))
    if port == 465:
        with smtplib.SMTP_SSL(hote, port, timeout=30) as s:
            if user:
                s.login(user, mdp)
            s.send_message(msg)
    else:
        with smtplib.SMTP(hote, port, timeout=30) as s:
            s.starttls()
            if user:
                s.login(user, mdp)
            s.send_message(msg)
    print(f"[ok] email envoyé à {dest}")


# ------------------------------------------------------------------------ main

def ajoute_entreprise(config: Path, url: str, nom: str | None) -> int:
    entree = sources_ats.detecte_ats(url, nom)
    cfg = json.loads(config.read_text(encoding="utf-8"))
    deja = [x for x in cfg["entreprises"] if x.get("ats") == entree["ats"]
            and (x.get("id"), x.get("url")) == (entree.get("id"), entree.get("url"))]
    if deja:
        print(f"[info] déjà présente : {deja[0]['nom']}")
        return 0
    cfg["entreprises"].append(entree)
    config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] ajoutée : {json.dumps(entree, ensure_ascii=False)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Veille alternance et CDI junior")
    ap.add_argument("--config", default=str(ICI / "config.json"))
    ap.add_argument("--previous", help="offers.json du passage précédent (chemin ou URL)")
    ap.add_argument("--out", default="public")
    ap.add_argument("--page-url", help="adresse de la page, citée dans l'email")
    ap.add_argument("--ajouter", metavar="URL", help="ajoute une entreprise depuis l'URL de sa page carrière")
    ap.add_argument("--nom", help="nom de l'entreprise (avec --ajouter)")
    args = ap.parse_args(argv)

    if args.ajouter:
        return ajoute_entreprise(Path(args.config), args.ajouter, args.nom)

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    profil = Profil(cfg.get("filtres") or {})
    quand = now_utc()
    offres, alertes, bilan = collecte(cfg, profil)
    for o in offres:  # inutile de stocker les descriptions complètes
        o["description"] = ""
    toutes, nouvelles = fusionne(charge_precedent(args.previous), offres, quand,
                                 int(cfg.get("conserver_jours", 30)))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "offers.json").write_text(json.dumps({"maj": iso(quand), "offres": toutes},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "index.html").write_text(page_html(toutes, {o["id"] for o in nouvelles}, quand, alertes, bilan),
                                    encoding="utf-8")
    print(f"[ok] {len(toutes)} offres publiées, dont {len(nouvelles)} nouvelles -> {out}/")
    for a in alertes:
        print(f"[alerte] {a}", file=sys.stderr)
    try:
        envoie_email(nouvelles, args.page_url)
    except Exception as ex:
        print(f"[alerte] email non envoyé : {ex}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
