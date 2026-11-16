"""Classement des offres : alternance ou CDI, métier visé, niveau junior, France."""
from __future__ import annotations

import html
import re
import unicodedata

# --------------------------------------------------------------------- texte

def normalise(texte: str | None) -> str:
    """Minuscules, sans accents, espaces simplifiés : « Sécurité » -> « securite »."""
    texte = unicodedata.normalize("NFKD", texte or "")
    texte = "".join(c for c in texte if not unicodedata.combining(c)).lower()
    texte = texte.replace("’", "'")
    # Écriture inclusive : « ingénieur(e) », « alternant.e », « ingénieur·e » -> forme courte.
    texte = re.sub(r"(?<=[a-z])(?:\((?:e|ne|se)\)|[.·](?:e|ne|se)\b)", "", texte)
    texte = re.sub(r"\bingenieure\b", "ingenieur", texte)
    return re.sub(r"\s+", " ", texte).strip()


def texte_brut(fragment: str | None) -> str:
    """HTML (éventuellement échappé, comme chez Greenhouse) -> texte simple."""
    t = html.unescape(html.unescape(fragment or ""))
    t = re.sub(r"<(br|/p|/li|/div|/h\d)\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"[ \t\r\f\v]+", " ", t).strip()


def motif(mots: list[str]) -> re.Pattern | None:
    """Liste de mots ou d'expressions -> expression régulière « mot entier »."""
    mots = sorted({normalise(m) for m in mots if m and m.strip()}, key=len, reverse=True)
    if not mots:
        return None
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(m) for m in mots) + r")(?![a-z0-9])")


def trouve(p: re.Pattern | None, texte: str) -> bool:
    return bool(p and p.search(texte))


# ------------------------------------------------------------- vocabulaire fixe

ALTERNANCE = motif([
    "alternance", "alternant", "alternante", "alternant(e)", "alternant.e", "apprenti", "apprentie",
    "apprenti(e)", "apprenti.e", "apprentissage", "apprenticeship", "apprentice", "work-study",
    "work study", "contrat pro", "contrat de professionnalisation", "professionnalisation",
])
STAGE = motif(["stage", "stagiaire", "intern", "internship", "v.i.e", "vie contract", "thesis", "these", "doctorant", "phd", "cifre"])
NON_CDI = motif(["cdd", "freelance", "free-lance", "independant", "interim", "temporary", "temporaire",
                 "fixed-term", "fixed term", "part-time", "temps partiel", "mission de"])
CONTRAT_NON_CDI = motif(["contract", "contractor", "freelance", "temporary", "intern", "internship",
                         "cdd", "fixed term", "fixed-term", "part-time", "part time", "apprenticeship",
                         "apprentissage", "alternance", "stage"])
SENIOR = motif([
    "senior", "sr", "sr.", "lead", "leader", "principal", "staff", "manager", "head",
    "director", "directeur", "directrice", "chef", "architect", "architecte", "expert", "experte",
    "confirme", "confirmee", "confirme(e)", "experimente", "experimentee", "experienced", "vp",
    "responsable", "tech lead", "team lead", "iii", "iv",
])
JUNIOR = motif([
    "junior", "jr", "debutant", "debutante", "debutant(e)", "jeune diplome", "jeune diplomee",
    "jeune diplome(e)", "entry level", "entry-level", "graduate", "new grad", "early career",
    "premier emploi", "0-2 ans", "0 a 2 ans", "0 a 1 an",
])
FRANCE = motif([
    "france", "fr", "paris", "ile-de-france", "ile de france", "idf", "lyon", "toulouse", "nantes",
    "lille", "bordeaux", "marseille", "nice", "sophia antipolis", "sophia-antipolis", "rennes",
    "grenoble", "montpellier", "strasbourg", "caen", "rouen", "nancy", "metz", "brest", "blagnac",
    "colomiers", "labege", "massy", "issy-les-moulineaux", "boulogne-billancourt", "nanterre",
    "la defense", "puteaux", "courbevoie", "levallois-perret", "saint-denis", "velizy",
    "velizy-villacoublay", "elancourt", "guyancourt", "saclay", "palaiseau", "meudon", "suresnes",
    "clichy", "montrouge", "ivry-sur-seine", "vincennes", "cergy", "merignac", "aix-en-provence",
    "tours", "orleans", "dijon", "clermont-ferrand", "angers", "le mans", "limoges", "poitiers",
    "pau", "annecy", "amiens", "reims", "le havre", "mulhouse", "besancon", "toulon",
    "saint-etienne", "valence", "lannion", "cesson-sevigne", "marignane", "bayonne", "niort",
])

# Années d'expérience exigées (texte déjà normalisé).
_ANS = r"(?:ans?|annees?|years?|yrs?)"
EXPERIENCE = [
    re.compile(r"(?:minimum|au moins|at least|min\.?)\s*(?:de\s+|d')?(\d{1,2})\s*\+?\s*" + _ANS),
    re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*" + _ANS + r"\s*(?:minimum\s*)?(?:d'|de |of )?\s*(?:solide |significative |professional |relevant |hands-on |industry |proven |work |professionnelle )*(?:experience|exp\.)"),
    re.compile(r"(\d{1,2})\s*(?:a|-|to|–)\s*\d{1,2}\s*" + _ANS),
    re.compile(r"experience\s+(?:professionnelle\s+)?(?:significative\s+|reussie\s+|solide\s+)?(?:de|d'au moins|d'environ|minimum de)\s*(\d{1,2})\s*" + _ANS),
    re.compile(r"(\d{1,2})\s*\+\s*" + _ANS),
]


def annees_exigees(texte_normalise: str) -> int | None:
    """Plus petit nombre d'années d'expérience demandé (entre 1 et 15), sinon None."""
    vus = []
    for p in EXPERIENCE:
        for m in p.finditer(texte_normalise):
            n = int(m.group(1))
            if 1 <= n <= 15:
                vus.append(n)
    return min(vus) if vus else None


# ------------------------------------------------------------------ classement

class Profil:
    """Critères lus dans config.json (section « filtres »)."""

    def __init__(self, cfg: dict):
        self.alt_domaine = motif(cfg.get("alternance_domaines", []))
        self.cdi_metiers = motif(cfg.get("cdi_metiers", []))
        self.exclure = motif(cfg.get("exclure_titre", []))
        self.max_ans = int(cfg.get("cdi_experience_max_ans", 2))
        self.garder_non_precise = bool(cfg.get("cdi_garder_niveau_non_precise", True))
        self.france = bool(cfg.get("france_uniquement", True))
        self.villes = motif(cfg.get("villes", []))

    def lieu_texte_ok(self, texte: str) -> bool:
        """Test rapide sur un libellé de lieu seul (avant de lire le détail d'une offre)."""
        t = normalise(texte)
        if self.villes:
            return trouve(self.villes, t)
        return not self.france or trouve(FRANCE, t)

    def lieu_ok(self, o: dict) -> bool:
        lieu = normalise(f"{o.get('lieu') or ''} {o.get('lieu_detail') or ''}")
        pays = normalise(o.get("pays"))
        if self.villes and not trouve(self.villes, lieu):
            return False
        if not self.france:
            return True
        if pays:
            return pays in ("fr", "fra", "france")
        return trouve(FRANCE, lieu)

    def titre_interessant(self, titre: str) -> bool:
        """Pré-filtre sur le seul titre, pour éviter de télécharger des détails inutiles."""
        t = normalise(titre)
        if trouve(self.exclure, t):
            return False
        if trouve(ALTERNANCE, t):
            return trouve(self.alt_domaine, t)
        return trouve(self.cdi_metiers, t)

    def classe(self, o: dict) -> dict | None:
        """Complète l'offre (type, niveau) si elle correspond au profil, sinon None."""
        titre = normalise(o.get("titre"))
        contrat = normalise(o.get("contrat"))
        desc = normalise(o.get("description"))
        if trouve(self.exclure, titre) or not self.lieu_ok(o):
            return None

        alternance = o.get("type") == "Alternance" or trouve(ALTERNANCE, titre) or trouve(ALTERNANCE, contrat)
        if alternance:
            if not trouve(self.alt_domaine, titre):
                return None
            o["type"], o["niveau"] = "Alternance", ""
            return o

        if o.get("type") not in (None, "", "CDI"):
            return None
        if trouve(STAGE, titre) or trouve(NON_CDI, titre) or trouve(CONTRAT_NON_CDI, contrat):
            return None
        if not trouve(self.cdi_metiers, titre):
            return None

        junior_explicite = trouve(JUNIOR, titre) or o.get("niveau_source") == "junior"
        if not junior_explicite:
            if trouve(SENIOR, titre) or o.get("niveau_source") == "senior":
                return None
        ans = o.get("annees_source")
        if ans is None:
            ans = annees_exigees(desc)
        if ans is not None and ans > self.max_ans and not trouve(JUNIOR, titre):
            return None
        if junior_explicite or trouve(JUNIOR, desc) or (ans is not None and ans <= self.max_ans):
            niveau = "Junior"
        elif self.garder_non_precise:
            niveau = "Niveau non précisé"
        else:
            return None
        o["type"], o["niveau"] = "CDI", niveau
        return o
