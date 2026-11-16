"""Tests hors ligne : toutes les réponses HTTP sont simulées."""
import datetime as dt
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
import filtres  # noqa: E402
import reseau  # noqa: E402
import sources_ats  # noqa: E402
import veille  # noqa: E402

CFG = json.loads((RACINE / "config.json").read_text(encoding="utf-8"))
PROFIL = filtres.Profil(CFG["filtres"])
FIX = Path(__file__).with_name("fixtures")


def offre(titre, lieu="Paris, France", description="", contrat="", **kw):
    return dict(id="t:" + titre, titre=titre, entreprise="X", lieu=lieu, lieu_detail="", pays="",
                contrat=contrat, description=description, url="https://x.fr", source="test", **kw)


def garde(o):
    r = PROFIL.classe(o)
    return None if r is None else (r["type"], r["niveau"])


# ---------------------------------------------------------------- filtres

class TestFiltresAlternance(unittest.TestCase):
    def test_domaines_cibles(self):
        self.assertEqual(garde(offre("Alternance - Ingénieur(e) DevSecOps H/F")), ("Alternance", ""))
        self.assertEqual(garde(offre("Apprenti.e Analyste SOC")), ("Alternance", ""))
        self.assertEqual(garde(offre("Cybersecurity Apprentice", "Toulouse")), ("Alternance", ""))
        self.assertEqual(garde(offre("Ingénieur DevOps", contrat="Apprenticeship")), ("Alternance", ""))

    def test_hors_domaine_ou_exclu(self):
        self.assertIsNone(garde(offre("Alternance Comptable")))
        self.assertIsNone(garde(offre("Alternant HSE - Sécurité au travail")))
        self.assertIsNone(garde(offre("Alternance commercial cybersécurité")))

    def test_france_uniquement(self):
        self.assertIsNone(garde(offre("Alternance DevOps", "Hamburg, Germany")))
        self.assertEqual(garde(offre("Alternance DevOps", "Blagnac")), ("Alternance", ""))


class TestFiltresCDI(unittest.TestCase):
    def test_junior_explicite(self):
        self.assertEqual(garde(offre("Junior DevOps Engineer")), ("CDI", "Junior"))
        self.assertEqual(garde(offre("Ingénieur Cloud débutant(e)")), ("CDI", "Junior"))
        # Le titre « junior » l'emporte sur l'expérience demandée.
        self.assertEqual(garde(offre("SRE Junior", description="3 ans d'expérience")), ("CDI", "Junior"))

    def test_seniors_exclus(self):
        for t in ["Senior Cloud Engineer", "Lead SRE", "Staff Platform Engineer", "DevOps Architect",
                  "Ingénieur DevOps confirmé", "Head of Infrastructure Engineering"]:
            self.assertIsNone(garde(offre(t)), t)

    def test_experience_lue_dans_la_description(self):
        self.assertIsNone(garde(offre("DevOps Engineer", description="You have 5+ years of experience")))
        self.assertIsNone(garde(offre("Ingénieur Cloud", description="Minimum 3 ans en production")))
        self.assertIsNone(garde(offre("Cloud Engineer", description="3 à 5 ans d'expérience")))
        self.assertEqual(garde(offre("Cloud Engineer", description="2 ans d'expérience sur AWS")), ("CDI", "Junior"))
        self.assertEqual(garde(offre("Platform Engineer", description="Rejoins l'équipe plateforme.")),
                         ("CDI", "Niveau non précisé"))
        # Une ancienneté d'entreprise n'est pas une exigence d'expérience.
        self.assertEqual(filtres.annees_exigees(filtres.normalise("Créée il y a plus de 10 ans")), None)

    def test_metiers_cibles(self):
        for t in ["DevSecOps Engineer", "Cloud Security Engineer", "Platform Engineer", "CloudOps Engineer",
                  "Ingénieure Infrastructure", "Ingénieur Systèmes Linux", "CI/CD Engineer",
                  "Site Reliability Engineer"]:
            self.assertIsNotNone(garde(offre(t)), t)
        for t in ["Data Scientist", "Ingénieur Système Embarqué", "Cloud Sales Engineer", "Product Manager"]:
            self.assertIsNone(garde(offre(t)), t)

    def test_contrats_non_cdi(self):
        self.assertIsNone(garde(offre("SRE Intern")))
        self.assertIsNone(garde(offre("Stage DevOps")))
        self.assertIsNone(garde(offre("DevOps Engineer", contrat="Contract")))
        self.assertIsNone(garde(offre("DevOps Engineer (CDD 6 mois)")))

    def test_niveau_fourni_par_la_source(self):
        self.assertIsNone(garde(offre("DevOps Engineer", niveau_source="senior")))
        self.assertEqual(garde(offre("DevOps Engineer", annees_source=0)), ("CDI", "Junior"))


# -------------------------------------------------------------- connecteurs

GH = {"jobs": [
    {"id": 1, "title": "Junior Site Reliability Engineer", "absolute_url": "https://boards.greenhouse.io/x/jobs/1",
     "location": {"name": "Paris"}, "first_published": "2026-11-12T09:00:00-05:00",
     "content": "&lt;p&gt;0-2 years of experience&lt;/p&gt;", "offices": [{"name": "Paris", "location": "Paris, France"}]},
    {"id": 2, "title": "Senior DevOps Engineer", "absolute_url": "https://boards.greenhouse.io/x/jobs/2",
     "location": {"name": "Paris, France"}, "content": "", "offices": []},
    {"id": 3, "title": "Cloud Engineer", "absolute_url": "https://boards.greenhouse.io/x/jobs/3",
     "location": {"name": "New York"}, "content": "", "offices": []},
]}
LEVER = [
    {"id": "a1", "text": "Alternance - Ingénieur DevSecOps", "hostedUrl": "https://jobs.lever.co/x/a1",
     "createdAt": 1762934400000, "country": "FR",
     "categories": {"location": "Paris", "commitment": "Alternance", "allLocations": ["Paris"]},
     "descriptionPlain": "Pipeline sécurisé", "lists": [{"text": "Profil", "content": "<li>Kubernetes</li>"}]},
    {"id": "a2", "text": "DevOps Engineer", "hostedUrl": "https://jobs.lever.co/x/a2", "createdAt": 1762934400000,
     "country": "FR", "categories": {"location": "Lille", "commitment": "CDI"},
     "descriptionPlain": "", "lists": [{"text": "Profil", "content": "<li>4+ years of experience</li>"}]},
]
SR_LISTE = {"totalFound": 2, "content": [
    {"id": "100", "name": "Cloud Engineer Junior", "releasedDate": "2026-11-10T08:00:00Z",
     "location": {"city": "Toulouse", "country": "fr"}, "typeOfEmployment": {"label": "Full-time"},
     "experienceLevel": {"id": "entry_level"}},
    {"id": "101", "name": "Comptable", "location": {"city": "Paris", "country": "fr"}},
]}
SR_DETAIL = {"jobAd": {"sections": {"jobDescription": {"text": "<p>AWS, Terraform</p>"},
                                    "qualifications": {"text": "<p>Jeune diplômé accepté</p>"}}}}
RECRUITEE = {"offers": [
    {"id": 7, "title": "Ingénieur·e Systèmes Linux", "careers_url": "https://x.recruitee.com/o/sys",
     "location": "Nantes, France", "country_code": "FR", "employment_type_code": "fulltime_permanent",
     "experience_code": "entry_level", "description": "<p>Linux</p>", "requirements": "",
     "published_at": "2026-11-11 10:00:00 UTC"}]}
WD_RECHERCHE = {"total": 3, "jobPostings": [
    {"title": "ALT 2027 - Apprenti(e) Ingénieur Cybersécurité (F/H)", "externalPath": "/job/Toulouse/Apprenti_JR1",
     "locationsText": "Toulouse", "postedOn": "Posted Today"},
    {"title": "Senior Cloud Architect", "externalPath": "/job/Hamburg/Senior_JR2",
     "locationsText": "Hamburg", "postedOn": "Posted 3 Days Ago"},
    {"title": "Junior DevOps Engineer", "externalPath": "/job/Multi/DevOps_JR3",
     "locationsText": "2 Locations", "postedOn": "Posted 2 Days Ago"},
]}
WD_DETAILS = {
    "/job/Toulouse/Apprenti_JR1": {"jobPostingInfo": {"title": "ALT 2027 - Apprenti(e) Ingénieur Cybersécurité (F/H)",
        "jobDescription": "<p>SOC, SIEM</p>", "location": "Toulouse", "country": {"descriptor": "France"},
        "externalUrl": "https://ag.wd3.myworkdayjobs.com/Airbus/job/Toulouse/Apprenti_JR1", "timeType": "Full time"}},
    "/job/Multi/DevOps_JR3": {"jobPostingInfo": {"title": "Junior DevOps Engineer", "jobDescription": "",
        "location": "Blagnac", "country": {"descriptor": "France"}, "timeType": "Full time"}},
}


def routeur(url, methode="GET", corps_json=None, formulaire=None, entetes=None):
    if "inexistant" in url:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    if "boards-api.greenhouse.io" in url:
        return 200, GH
    if "api.lever.co" in url:
        return 200, LEVER
    if "api.smartrecruiters.com" in url:
        return 200, SR_DETAIL if url.split("?")[0].endswith("/100") else SR_LISTE
    if "recruitee.com" in url:
        return 200, RECRUITEE
    if "myworkdayjobs.com" in url:
        if methode == "POST":
            return 200, WD_RECHERCHE if corps_json["offset"] == 0 else {"total": 3, "jobPostings": []}
        chemin = url.split("/Airbus", 1)[1]
        return 200, WD_DETAILS[chemin]
    if "api.apprentissage.beta.gouv.fr" in url:
        return 200, json.loads((FIX / "lba.json").read_text(encoding="utf-8"))
    if "francetravail" in url:
        if "access_token" in url:
            return 200, {"access_token": "t"}
        return 200, json.loads((FIX / "france_travail.json").read_text(encoding="utf-8"))
    raise AssertionError(url)


class TestConnecteurs(unittest.TestCase):
    def setUp(self):
        p1 = mock.patch.object(reseau, "requete", side_effect=routeur)
        p2 = mock.patch.object(reseau, "pause")
        self.req = p1.start()
        p2.start()
        self.addCleanup(mock.patch.stopall)

    def retenues(self, offres):
        return [(o["titre"], o["type"], o["niveau"]) for o in offres if PROFIL.classe(o)]

    def test_greenhouse(self):
        o = sources_ats.greenhouse({"nom": "X", "id": "x"}, PROFIL.titre_interessant)
        self.assertEqual(len(o), 3)
        self.assertIn("0-2 years", o[0]["description"])  # HTML échappé décodé
        self.assertEqual(self.retenues(o), [("Junior Site Reliability Engineer", "CDI", "Junior")])

    def test_lever(self):
        o = sources_ats.lever({"nom": "X", "id": "x"}, PROFIL.titre_interessant)
        self.assertEqual(o[0]["date"][:10], "2025-11-12")
        self.assertEqual(self.retenues(o), [("Alternance - Ingénieur DevSecOps", "Alternance", "")])

    def test_smartrecruiters(self):
        o = sources_ats.smartrecruiters({"nom": "X", "id": "x"}, PROFIL.titre_interessant)
        self.assertEqual(len(o), 1)  # « Comptable » écarté avant le détail
        self.assertIn("Jeune diplômé", o[0]["description"])
        self.assertEqual(o[0]["url"], "https://jobs.smartrecruiters.com/x/100")
        self.assertEqual(self.retenues(o), [("Cloud Engineer Junior", "CDI", "Junior")])
        self.assertIn("country=fr", self.req.call_args_list[0].args[0])

    def test_recruitee(self):
        o = sources_ats.recruitee({"nom": "X", "id": "x"}, PROFIL.titre_interessant)
        self.assertEqual(self.retenues(o), [("Ingénieur·e Systèmes Linux", "CDI", "Junior")])

    def test_workday(self):
        e = {"nom": "Airbus", "ats": "workday", "url": "https://ag.wd3.myworkdayjobs.com/fr-FR/Airbus"}
        o = sources_ats.workday(e, PROFIL.titre_interessant, ["alternance"], PROFIL.lieu_texte_ok)
        self.assertEqual(sorted(x["titre"] for x in o),
                         ["ALT 2027 - Apprenti(e) Ingénieur Cybersécurité (F/H)", "Junior DevOps Engineer"])
        self.assertEqual(sorted(self.retenues(o)), [
            ("ALT 2027 - Apprenti(e) Ingénieur Cybersécurité (F/H)", "Alternance", ""),
            ("Junior DevOps Engineer", "CDI", "Junior")])
        post = self.req.call_args_list[0]
        self.assertEqual(post.args[0], "https://ag.wd3.myworkdayjobs.com/wday/cxs/ag/Airbus/jobs")
        self.assertEqual(post.kwargs["corps_json"]["searchText"], "alternance")
        self.assertTrue(all(x["url"].startswith("https://ag.wd3.myworkdayjobs.com/Airbus/job/") for x in o))


class TestDetection(unittest.TestCase):
    def test_urls(self):
        d = sources_ats.detecte_ats
        self.assertEqual(d("https://ag.wd3.myworkdayjobs.com/fr-FR/Airbus/job/x", "Airbus"),
                         {"nom": "Airbus", "ats": "workday", "url": "https://ag.wd3.myworkdayjobs.com/Airbus"})
        self.assertEqual(d("https://jobs.lever.co/qonto/30ebfbf7"), {"nom": "qonto", "ats": "lever", "id": "qonto"})
        self.assertEqual(d("https://job-boards.greenhouse.io/ddome/jobs/794", "DataDome")["id"], "ddome")
        self.assertEqual(d("https://job-boards.eu.greenhouse.io/wakam/jobs/1")["region"], "eu")
        self.assertEqual(d("https://jobs.smartrecruiters.com/Ubisoft2/123")["id"], "Ubisoft2")
        self.assertEqual(d("https://acme.recruitee.com/o/devops")["id"], "acme")
        with self.assertRaises(ValueError):
            d("https://careers.thalesgroup.com/fr/fr/job/123")


# -------------------------------------------------------------- bout en bout

class TestBoutEnBout(unittest.TestCase):
    def test_main(self):
        env = {"LBA_API_TOKEN": "l", "FT_CLIENT_ID": "i", "FT_CLIENT_SECRET": "s"}
        cfg = dict(CFG, entreprises=[
            {"nom": "Lever X", "ats": "lever", "id": "x"},
            {"nom": "Airbus", "ats": "workday", "url": "https://ag.wd3.myworkdayjobs.com/Airbus"},
            {"nom": "Cassée", "ats": "lever", "id": "inexistant"},
        ])
        with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", env), \
                mock.patch.object(reseau, "requete", side_effect=routeur), mock.patch.object(reseau, "pause"):
            conf = Path(d) / "config.json"
            conf.write_text(json.dumps(cfg), encoding="utf-8")
            veille.main(["--config", str(conf), "--out", d, "--previous", str(Path(d) / "absent.json")])
            donnees = json.loads((Path(d) / "offers.json").read_text(encoding="utf-8"))
            titres = sorted(o["titre"] for o in donnees["offres"])
            self.assertIn("Alternance - Ingénieur DevSecOps", titres)
            self.assertIn("Junior DevOps Engineer", titres)
            self.assertIn("Alternance Analyste SOC - Mastère cybersécurité", titres)  # API LBA
            self.assertNotIn("DevOps Engineer", titres)        # 4+ ans demandés
            self.assertNotIn("Assistant comptable (H/F)", titres)
            self.assertIn("Ingénieur DevOps (H/F)", titres)          # France Travail, débutant accepté
            self.assertNotIn("Ingénieur Cloud (H/F)", titres)        # 5 ans exigés
            page = (Path(d) / "index.html").read_text(encoding="utf-8")
            self.assertIn("1 source(s) en erreur", page)
            self.assertIn("Cassée", page)
            self.assertNotIn("<script>alert", page)

            # Deuxième passage : plus rien de nouveau.
            veille.main(["--config", str(conf), "--out", d, "--previous", str(Path(d) / "offers.json")])
            page = (Path(d) / "index.html").read_text(encoding="utf-8")
            self.assertIn("· 0 nouvelle(s)", page)

    def test_ajouter(self):
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / "config.json"
            conf.write_text(json.dumps({"entreprises": []}), encoding="utf-8")
            veille.main(["--config", str(conf), "--ajouter", "https://jobs.lever.co/qonto/abc", "--nom", "Qonto"])
            veille.main(["--config", str(conf), "--ajouter", "https://jobs.lever.co/qonto/def"])
            self.assertEqual(json.loads(conf.read_text())["entreprises"],
                             [{"nom": "Qonto", "ats": "lever", "id": "qonto"}])


class TestFusion(unittest.TestCase):
    def test_nouvelles_et_expiration(self):
        t0 = dt.datetime(2026, 11, 13, 6, 0, tzinfo=dt.timezone.utc)
        toutes, nouv = veille.fusionne([], [{"id": "a"}], t0, 30)
        self.assertEqual([o["id"] for o in nouv], ["a"])
        toutes, nouv = veille.fusionne(toutes, [{"id": "a"}, {"id": "b"}], t0 + dt.timedelta(hours=12), 30)
        self.assertEqual([o["id"] for o in nouv], ["b"])
        toutes, _ = veille.fusionne(toutes, [], t0 + dt.timedelta(days=40), 30)
        self.assertEqual(toutes, [])


if __name__ == "__main__":
    unittest.main()
