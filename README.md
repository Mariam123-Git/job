# Veille alternance et CDI junior : offres lues sur les sites carrières

Deux fois par jour (7 h et 19 h), un pipeline GitLab lit les offres **directement sur les
sites carrières** des entreprises de `config.json`, garde celles qui te correspondent,
publie une page avec les **nouveautés** et peut t'envoyer un email.

| Recherche | Ce qui est gardé |
|---|---|
| **Alternance** | cybersécurité (SOC, SIEM, sécurité, pentest, GRC…), DevOps, DevSecOps, SRE, CI/CD |
| **CDI junior** | DevOps, Cloud, DevSecOps, Cloud Security, Platform, SRE, CloudOps, ingénieur infrastructure / systèmes, CI/CD |

Toujours en **France**. Les stages, CDD, freelances et le hors-sujet (embarqué, commercial, HSE…) sont écartés.

## Comment le niveau junior est vérifié (CDI)

1. Titre contenant *junior, débutant, jeune diplômé, graduate, entry level* → badge **Junior**.
2. Titre contenant *senior, lead, staff, principal, manager, architecte, expert, confirmé…* → écartée.
3. Sinon, la description est lue : « 3 ans d'expérience », « 5+ years of experience »,
   « minimum 3 ans », « 3 à 5 ans »… Au-delà de **2 ans** → écartée. 2 ans ou moins → **Junior**.
4. Rien d'indiqué → gardée avec le badge **Niveau non précisé** (beaucoup d'offres accessibles
   aux juniors ne le disent pas). Pour les écarter : `"cdi_garder_niveau_non_precise": false`.

Le niveau donné par le site lui-même est aussi utilisé (SmartRecruiters, Recruitee, France Travail).

## Sites carrières pris en charge

| Logiciel (ATS) | Accès | Exemples déjà configurés |
|---|---|---|
| Lever | API publique officielle | Scaleway, Mistral AI, Theodo, Qonto, Malt, Brevo, Pigment… |
| Greenhouse | API publique officielle | Datadog, Doctolib, DataDome, Dashlane, Tenable, Dataiku, Algolia, Mirakl… |
| SmartRecruiters | API publique officielle | (à ajouter) |
| Recruitee | API publique officielle | (à ajouter) |
| Workday | JSON utilisé par la page carrière, recherches ciblées, 1 s entre deux appels | Airbus |
| **Navigateur (Playwright)** | Chromium sans écran : lit n'importe quel site (JSON-LD puis sélecteurs CSS) | grands groupes, sites maison |

### Ajouter une entreprise

Ouvre une offre de l'entreprise sur son site carrières. Si l'adresse contient
`myworkdayjobs.com`, `lever.co`, `greenhouse.io`, `smartrecruiters.com` ou `recruitee.com` :

```bash
python3 veille.py --ajouter "https://jobs.lever.co/qonto/30ebfbf7-..." --nom "Qonto"
```

L'entreprise est ajoutée à `config.json`. Pousse le fichier sur GitLab.

**Sites maison et grands groupes (Thales, Safran, Orange, Capgemini, Sopra Steria, et tout
site SuccessFactors / iCIMS / Talentsoft / Oracle)** : pas d'API propre. Deux solutions :
- le **mode navigateur** ci-dessous, qui lit directement leur page de résultats ;
- les **compléments France Travail et La bonne alternance** (section 2), qui republient une
  grande partie de ces offres sans aucun réglage.

## Mode navigateur (sites sans API)

Pour un site sans API, on ouvre sa page de résultats dans un Chromium sans écran et on lit
les offres. Deux stratégies, automatiques, dans l'ordre :

1. **JSON-LD** (par défaut, aucun réglage) : la plupart des sites carrières publient des
   données structurées « JobPosting » pour Google. Elles sont lues directement.
2. **Sélecteurs CSS** (repli) : si le site n'a pas de JSON-LD, tu fournis un bloc `dom`.

Ajoute une entrée dans `config.json` → `entreprises` (exemple inactif déjà présent) :

```json
{
  "nom": "Carrefour",
  "ats": "navigateur",
  "url": "https://.../search?keywords=alternance%20cybersecurite&location=France",
  "dom": {"carte": "li.job-tile", "titre": "a.job-title", "lien": "a.job-title",
          "lieu": ".job-location", "attendre": "li.job-tile"}
}
```

- `url` : **la page de résultats déjà filtrée** (métier + France) telle qu'elle apparaît dans
  ton navigateur après avoir tapé la recherche. C'est le seul champ indispensable.
- `dom` : à renseigner **seulement si** le site n'a pas de JSON-LD. Pour trouver les sélecteurs :
  clic droit → « Inspecter » sur une offre ; `carte` = le bloc d'une offre, `titre`/`lien` = le
  lien du titre, `lieu` = le texte du lieu, `attendre` = un sélecteur présent une fois la liste chargée.

**À savoir honnêtement** : ce mode est plus fragile que les API. Si un site change sa structure,
il peut cesser de remonter des offres — ça apparaît alors dans « Sources interrogées » (0 offre) ou
en erreur. Les sélecteurs sont à vérifier au premier passage réel, car ils dépendent de chaque site.
Le mode navigateur a besoin de Chromium : les pipelines fournis utilisent déjà l'image Playwright.

## 1. Mise en place du pipeline

Deux pipelines sont fournis, prêts à l'emploi, tous deux basés sur l'image Playwright
(donc le mode navigateur fonctionne) : choisis-en un.

### Option A — GitHub Actions (`.github/workflows/veille.yml`)

1. Crée un dépôt et pousse ces fichiers.
2. **Settings → Pages → Source : GitHub Actions**. Note l'adresse de la page.
3. **Settings → Secrets and variables → Actions** :
   - onglet **Variables** : `PAGE_URL` = l'adresse de ta page (ex. `https://ton-compte.github.io/veille`).
   - onglet **Secrets** : les clés facultatives de la section 2 (API, email).
4. **Actions → Veille alternance et CDI → Run workflow** pour un premier passage.
   Ensuite, exécution automatique à 6 h et 18 h UTC (≈ 7 h et 19 h à Paris en hiver ; GitHub
   n'utilise que l'heure UTC, décaler d'une heure en été si besoin).
   Le relevé précédent est conservé via le cache, donc compatible avec un dépôt privé.

### Option B — GitLab CI (`.gitlab-ci.yml`)

1. Crée un projet (privé de préférence) et pousse ces fichiers.
2. **Build → Pipeline schedules → New schedule** : interval `0 7,19 * * *`, timezone `Paris`, branche `main`.
3. **Build → Pipelines → Run pipeline** pour un premier passage.
   La page est à l'adresse indiquée dans **Deploy → Pages** (privée via Settings → General →
   Visibility → Pages : *Only Project Members*).

Dans les deux cas, ouvre « Sources interrogées » en haut de la page : pour chaque entreprise,
le nombre d'offres lues et retenues. Une source en erreur est signalée sans bloquer les autres.

## 2. Options (Settings → CI/CD → Variables, cases *Masked* cochées)

| Variable | Rôle |
|---|---|
| `LBA_API_TOKEN` | complément alternance (clé gratuite sur api.apprentissage.beta.gouv.fr) |
| `FT_CLIENT_ID`, `FT_CLIENT_SECRET` | complément CDI (francetravail.io, API « Offres d'emploi v2 ») |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO` | email des nouveautés (Gmail : `smtp.gmail.com`, port `465`, mot de passe d'application) |

## 3. Réglages : `config.json` → `filtres`

| Clé | Rôle |
|---|---|
| `villes` | `[]` = toute la France ; sinon par ex. `["toulouse", "blagnac", "paris", "caen"]` |
| `alternance_domaines` | mots du titre pour l'alternance |
| `cdi_metiers` | métiers visés en CDI |
| `exclure_titre` | mots qui écartent une offre |
| `cdi_experience_max_ans` | expérience maximale acceptée (2 par défaut) |
| `cdi_garder_niveau_non_precise` | garder les CDI sans niveau indiqué |

Les mots sont comparés sans accents ni majuscules, en mot entier (« soc » ne correspond pas à « société »).

## Lancer en local

```bash
python3 veille.py --previous public/offers.json --out public
python3 -m unittest discover -s tests      # tests hors ligne
```
