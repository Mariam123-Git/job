"""Mode navigateur : extraction JSON-LD (pure) et validation du mécanisme Playwright
sur une page LOCALE (file://), sans aucun accès réseau."""
import sys
import tempfile
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
import filtres  # noqa: E402
import sources_navigateur as nav  # noqa: E402

PROFIL = filtres.Profil({
    "alternance_domaines": ["cyber", "cybersecurite", "devops", "devsecops"],
    "cdi_metiers": ["devops", "cloud engineer", "sre"], "exclure_titre": [],
})

# Page avec données structurées JSON-LD : un bloc isolé + un bloc @graph.
PAGE_JSONLD = """<!doctype html><html><head>
<script type="application/ld+json">
{"@context":"https://schema.org/","@type":"JobPosting",
 "title":"Alternance Ingénieur Cybersécurité (F/H)","datePosted":"2026-11-10",
 "employmentType":"INTERN","hiringOrganization":{"@type":"Organization","name":"GrandGroupe"},
 "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Toulouse",
   "addressCountry":{"name":"France"}}},
 "url":"https://careers.exemple.com/job/123","description":"<p>SOC, SIEM</p>"}
</script>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"WebSite","name":"Carrières"},
 {"@type":"JobPosting","title":"Cloud Engineer Junior","datePosted":"2026-11-09",
  "hiringOrganization":{"name":"GrandGroupe"},
  "jobLocation":{"address":{"addressLocality":"Paris","addressCountry":"FR"}},
  "mainEntityOfPage":{"@id":"https://careers.exemple.com/job/456"},
  "employmentType":["FULL_TIME"],"description":"AWS, Terraform"}]}
</script></head><body>Liste</body></html>"""

# Page SANS JSON-LD, lue par sélecteurs CSS.
PAGE_DOM = """<!doctype html><html><head><title>Offres</title></head><body>
<ul id="resultats">
  <li class="job-tile"><a class="job-title" href="https://careers.exemple.com/job/a">Alternance DevSecOps</a>
      <span class="job-location">Lyon, France</span></li>
  <li class="job-tile"><a class="job-title" href="https://x.test/job/b">SRE Junior</a>
      <span class="job-location">Nantes</span></li>
</ul></body></html>"""


class TestJsonLd(unittest.TestCase):
    def test_extraction(self):
        offres = nav.offres_depuis_jsonld(PAGE_JSONLD, "GrandGroupe", "https://careers.exemple.com/")
        self.assertEqual(sorted(o["titre"] for o in offres),
                         ["Alternance Ingénieur Cybersécurité (F/H)", "Cloud Engineer Junior"])
        cyber = next(o for o in offres if o["titre"].startswith("Alternance"))
        self.assertEqual(cyber["lieu"], "Toulouse")
        self.assertEqual(cyber["pays"], "France")
        self.assertEqual(cyber["contrat"], "INTERN")       # => sera filtré comme stage
        self.assertEqual(cyber["url"], "https://careers.exemple.com/job/123")
        self.assertEqual(cyber["date"], "2026-11-10")
        self.assertIn("SOC", cyber["description"])
        # URL reconstruite depuis mainEntityOfPage.@id
        cloud = next(o for o in offres if o["titre"].startswith("Cloud"))
        self.assertEqual(cloud["url"], "https://careers.exemple.com/job/456")

    def test_apres_classement(self):
        offres = nav.offres_depuis_jsonld(PAGE_JSONLD, "GrandGroupe", "https://careers.exemple.com/")
        classees = {(o["titre"], o["type"], o["niveau"]) for o in offres if PROFIL.classe(o)}
        # Le titre « Alternance … Cybersécurité » prime sur employmentType=INTERN,
        # et le Cloud Engineer Junior est gardé en CDI junior.
        self.assertEqual(classees, {
            ("Alternance Ingénieur Cybersécurité (F/H)", "Alternance", ""),
            ("Cloud Engineer Junior", "CDI", "Junior")})

    def test_url_non_http_rejetee(self):
        self.assertIsNone(nav._lien("javascript:alert(1)"))
        self.assertIsNone(nav._lien("data:text/html,x"))
        self.assertEqual(nav._lien("/job/1", "https://x.fr/"), "https://x.fr/job/1")

    def test_bloc_illisible_ignore(self):
        html = '<script type="application/ld+json">{cassé}</script>' + PAGE_JSONLD
        self.assertEqual(len(nav.offres_depuis_jsonld(html, "X")), 2)


@unittest.skipUnless(
    __import__("importlib").util.find_spec("playwright"), "Playwright non installé")
class TestNavigateurReel(unittest.TestCase):
    """Valide tout le mécanisme Playwright sur des fichiers LOCAUX (aucun réseau)."""

    def _ouvre(self, contenu, spec=None):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "page.html"
            f.write_text(contenu, encoding="utf-8")
            e = {"nom": "GrandGroupe", "ats": "navigateur", "url": f.as_uri(), "dom": spec}
            return nav.collecte(e, timeout_ms=15000, defilements=1)

    def test_jsonld_via_chromium(self):
        offres = self._ouvre(PAGE_JSONLD)
        self.assertEqual(len(offres), 2)
        self.assertTrue(any("Cybersécurité" in o["titre"] for o in offres))

    def test_repli_dom_via_chromium(self):
        spec = {"carte": "li.job-tile", "titre": "a.job-title", "lien": "a.job-title", "lieu": ".job-location"}
        offres = self._ouvre(PAGE_DOM, spec)
        self.assertEqual(sorted(o["titre"] for o in offres), ["Alternance DevSecOps", "SRE Junior"])
        a = next(o for o in offres if o["titre"] == "Alternance DevSecOps")
        self.assertTrue(a["url"].endswith("/job/a"))      # href rendu absolu par le navigateur
        self.assertEqual(a["lieu"], "Lyon, France")


if __name__ == "__main__":
    unittest.main()
