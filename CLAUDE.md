# CLAUDE.md — Référentiel de mappings OMOP (AP-HM / DIM)

Ce fichier est la référence du projet pour Claude. Le lire en entier avant toute modification.
En cas de conflit entre ce fichier et une demande ponctuelle, signaler le conflit avant d'agir.

---

## 1. Objectif

Plateforme web interne qui **versionne et expose la table `source_to_concept_map`** (mappings des codes
locaux AP-HM vers les concepts standards OMOP), alimentée par des fichiers CSV/Excel.

- Plusieurs centaines de milliers de mappings, toutes sources confondues.
- Chaque **release majeure** (v1.0, v2.0, …) correspond à un CDM généré par le pipeline ETL.
- Entre deux majeures existe **une seule release intermédiaire de staging** (ex. v1.1) : copie de la
  majeure précédente à laquelle on ajoute / corrige des mappings non encore validés.
- L'interface **reproduit l'ergonomie d'Athena** (https://athena.ohdsi.org/search-terms/start),
  appliquée aux mappings et non au catalogue des vocabulaires.

## 2. Périmètre

### Inclus
- Recherche à facettes dans `source_to_concept_map` (une release à la fois).
- Fiche d'un code source : détails, cibles, historique entre releases, audit.
- Import CSV/Excel en lot avec correspondance de colonnes et valeurs par défaut par colonne.
- Correction manuelle d'un mapping dans la release ouverte, avec sélecteur de concept cible.
- Gestion des releases : créer la staging, publier, promouvoir, diff entre deux releases.
- Colonnes personnalisées sur `source_to_concept_map` (stockées dans `extra jsonb`).
- Contrôle qualité des cibles et exports CSV (résultats de recherche, diff, release au format CDM).
- Onglet « Export » : properties (un fichier par domaine, `code=cible[,cible…]`), CSV CDM, CSV complet ;
  téléchargement ou écriture dans `data/30_properties` ; même export en ligne de commande et par l'API.
- Onglet « Comparer » : différences entre deux releases — métriques ligne à ligne (ajoutées, modifiées,
  supprimées, inchangées, taux de changement, solde, par vocabulaire et par domaine, origine : import ou
  correction manuelle, lignes à relire), puis vue par code source (transitions de statut, liste à facettes,
  export). Seules les lignes / codes qui ont changé sont listés.
- Onglet « Athena » : comparaison de nos mappings aux relations natives « Maps to » / « Maps to value »
  d'une base Athena enregistrée (connexion stockée, copie locale synchronisée à la demande).
  Ce n'est pas un navigateur de vocabulaire : seules les relations « Maps to » sont lues.

### Exclu (ne pas implémenter sans demande explicite)
- **Navigation dans le vocabulaire** : pas de page de recherche de concepts, pas de fiche concept,
  pas de navigation dans les relations ni la hiérarchie (seule exception : la comparaison aux « Maps to »
  d'Athena, demandée explicitement). Le schéma `vocab` est un référentiel technique en lecture seule.
- Authentification / gestion des droits (pas d'auth pour l'instant ; le nom de l'auteur est saisi
  librement et stocké dans un cookie).
- Toute écriture dans la base du CDM.
- Framework JS front (React, Vue, Angular…) et tout CDN.

## 3. Stack et contraintes

| Couche | Choix |
|---|---|
| Langage | Python 3.11+ |
| Web | FastAPI + Uvicorn |
| Rendu | Jinja2 côté serveur, Bootstrap 5, HTMX (fragments) |
| Accès BDD | SQLAlchemy 2.0 (ORM + Core) avec le driver psycopg 3 (`postgresql+psycopg://`) |
| Import | pandas + openpyxl (lecture), `COPY` psycopg (chargement) |
| BDD | PostgreSQL 16, base dédiée `omop_referentiel`, séparée du CDM |
| Config | pydantic-settings, fichier `.env` |
| Qualité | ruff (lint + format), mypy (mode standard), pytest |

Contraintes d'environnement (réseau hospitalier partiellement isolé) :
- **Aucun appel réseau à l'exécution** : Bootstrap, Bootstrap Icons et HTMX sont copiés dans
  `app/static/vendor/` et servis localement. Jamais de balise `<script src="https://…">`.
- Les dépendances Python sont figées dans `requirements.txt` (versions exactes) pour une
  installation via miroir pip interne ou wheels hors ligne. Le dossier `wheels/` contient ces wheels
  (CPython 3.12, Windows 64 bits) ; `scripts/install.ps1` installe hors ligne, `scripts/start.ps1`
  (ou `demarrer.cmd`) démarre l'application, `scripts/download_wheels.ps1` régénère les wheels.
- Doit tourner sur Linux (serveur) et se lancer aussi sur un poste Windows de développement.

## 4. Principe d'architecture

**Le DDL SQL est la seule source de vérité du schéma.** La logique de versionnement (copie de
release, publication, promotion, diff, verrouillage, audit) vit dans PostgreSQL (fonctions,
triggers, vues). L'application Python orchestre et affiche ; elle ne réimplémente pas cette logique.

- SQLAlchemy mappe des modèles sur les tables **existantes**. `Base.metadata.create_all()` est
  **interdit**, comme toute génération de schéma par l'ORM ou par Alembic autogenerate.
- ORM pour le CRUD simple (releases, colonnes personnalisées, édition d'un mapping).
- SQLAlchemy Core pour les requêtes dynamiques (recherche + facettes).
- `text()` pour appeler les fonctions PostgreSQL (`mapping.create_release`, `mapping.diff_releases`…).
- `COPY` psycopg pour les imports volumineux (jamais d'INSERT ligne à ligne via l'ORM).

### Arborescence cible

```
omop-referentiel/
├── CLAUDE.md
├── README.md
├── requirements.txt
├── demarrer.cmd                # double-clic : lance scripts/start.ps1
├── wheels/                     # dépendances Python hors ligne (pip --no-index)
├── pyproject.toml              # config ruff, mypy, pytest
├── .env.example
├── db/
│   ├── ddl/                    # migrations SQL numérotées, appliquées dans l'ordre
│   │   ├── 01_vocab_tables.sql
│   │   ├── 02_vocab_indexes.sql
│   │   ├── 03_mapping.sql
│   │   ├── 04_import_helpers.sql
│   │   ├── 05_diff_releases_perf.sql
│   │   ├── 06_audit_statement_trigger.sql
│   │   ├── 07_audit_update_hash_join.sql
│   │   ├── 08_diff_codes.sql
│   │   ├── 09_athena_reference.sql
│   │   ├── 10_search_text.sql
│   │   ├── 11_search_text_column.sql
│   │   └── 12_status_lifecycle_and_import_rollback.sql
│   └── seed/                   # jeux de données de démonstration / test
├── scripts/
│   ├── migrate.py              # applique db/ddl/*.sql non encore appliqués
│   ├── load_vocab.py           # charge CONCEPT.csv / VOCABULARY.csv Athena
│   ├── load_usagi_dir.py       # charge un répertoire d'exports Usagi (ex. data/) via le circuit d'import
│   ├── export_release.py       # export properties / CSV d'une release (tâche planifiée)
│   ├── install.ps1             # installation hors ligne (venv + wheels + .env + migrations)
│   ├── start.ps1               # démarrage (PostgreSQL portable optionnel, migrations, serveur)
│   └── download_wheels.ps1     # (poste connecté) téléchargement des wheels
├── app/
│   ├── main.py                 # création de l'app FastAPI, montage des routers et du static
│   ├── config.py               # Settings (pydantic-settings)
│   ├── db.py                   # engine, sessionmaker, dépendance get_session
│   ├── models/                 # modèles SQLAlchemy mappés sur les tables existantes
│   │   ├── release.py
│   │   ├── stcm.py
│   │   ├── import_batch.py
│   │   └── custom_column.py
│   ├── repositories/           # SEUL endroit contenant du SQL / des requêtes
│   │   ├── release_repo.py
│   │   ├── stcm_repo.py        # recherche, facettes, fiche code source
│   │   ├── vocab_repo.py       # lookup concept, sélecteur de cible
│   │   ├── import_repo.py
│   │   └── audit_repo.py
│   ├── services/               # règles métier, transactions
│   │   ├── release_service.py
│   │   ├── search_service.py
│   │   ├── import_service.py
│   │   └── export_service.py
│   ├── schemas/                # modèles pydantic (formulaires, filtres, DTO)
│   ├── routes/                 # routers FastAPI (pages + fragments HTMX)
│   │   ├── search.py
│   │   ├── mapping.py
│   │   ├── imports.py
│   │   ├── releases.py
│   │   └── api.py              # API JSON/CSV pour le pipeline
│   ├── templates/
│   │   ├── base.html
│   │   ├── pages/              # pages complètes
│   │   └── partials/           # fragments HTMX, préfixés par _
│   └── static/
│       ├── css/app.css
│       ├── js/app.js
│       └── vendor/             # bootstrap, bootstrap-icons, htmx (copies locales)
└── tests/
    ├── conftest.py
    ├── sql/                    # tests des fonctions / triggers PostgreSQL
    ├── services/
    └── routes/
```

Dépendances entre couches : `routes → services → repositories → db`. Une route n'appelle jamais
un repository directement, un template ne contient jamais de logique métier.

## 5. Modèle de données

Base `omop_referentiel`, deux schémas :

- **`vocab`** : `concept`, `vocabulary` au format OMOP v5.4, chargés depuis Athena. Lecture seule
  pour l'application.
- **`mapping`** : tout ce qui appartient au projet.

| Objet | Rôle |
|---|---|
| `mapping.release` | Releases : `label` (v1.0…), `kind` (`major`/`staging`), `status` (`open`/`frozen`/`published`/`archived`), parent, version de vocabulaire, référence du build CDM |
| `mapping.source_to_concept_map` | Mappings versionnés : colonnes CDM v5.4 + extensions AP-HM + `extra jsonb` |
| `mapping.custom_column` | Déclaration des colonnes personnalisées stockées dans `extra` |
| `mapping.import_batch` / `import_error` | Traçabilité de chaque import et lignes rejetées |
| `mapping.audit_log` | Historique UPDATE/DELETE ligne à ligne |
| `mapping.create_release()` | Crée une release en copiant toutes les lignes de la parente |
| `mapping.publish_release()` | Fige et publie une release, avec la référence du build CDM ; (12) les UNCHECKED deviennent APPROVED, refus s'il reste des FLAGGED |
| `mapping.import_effects()` / `mapping.rollback_import()` (12) | Lignes ajoutées / modifiées / supprimées par un import ; annulation complète tant que la release est ouverte |
| `mapping.promote_staging()` | Staging → nouvelle majeure ; la staging est archivée |
| `mapping.diff_releases()` | ADDED / REMOVED / MODIFIED (+ champs modifiés) entre deux releases |
| `mapping.v_mapping_quality` | Drapeau qualité de chaque cible contre `vocab.concept` |
| `mapping.v_stcm_published` | Dernière release publiée au format CDM strict (consommée par dbt) |
| `mapping.v_release_lineage` | Lignée des releases et volumétrie |
| `mapping.v_stcm_cdm` | Format CDM strict de toutes les releases, filtrable par `release_label` (export API) — 04 |
| `mapping.try_cast_date/int/numeric/boolean()` | Conversions tolérantes (NULL si invalide) pour les contrôles d'import — 04 |
| `mapping.diff_releases()` (05) | Réécrite à résultat identique : comparaison colonne à colonne, jsonb construit seulement pour les lignes différentes. **Toute nouvelle colonne de `source_to_concept_map` doit y être ajoutée** |
| `mapping.diff_codes()` (08) | Diff de deux releases regroupé par code : NEW_CODE / REMOVED_CODE / TARGET_CHANGED / MODIFIED, cibles avant / après |
| `mapping.athena_connection` (09) | Bases Athena enregistrées (une seule active ; mot de passe stocké, compte en lecture seule) |
| `mapping.athena_vocabulary_map` (09) | `source_vocabulary_id` local → `vocabulary_id` Athena, normalisation des codes (points, casse) |
| `mapping.athena_maps_to` (09) | Copie locale des concepts Athena paramétrés et de leurs « Maps to » valides (remplacée à chaque synchronisation) |
| `mapping.compare_athena()` (09) | Statut de chaque mapping face à Athena : SAME / DIFFERENT / MISSING_LOCAL / ATHENA_NO_MAPPING / CODE_NOT_IN_ATHENA |
| `mapping.normalize_text()` (10) | Minuscules sans accents (extension `unaccent`), IMMUTABLE |
| `source_to_concept_map.search_text` (11) | Colonne générée : code + description source normalisés, index trigram ; exclue de `diff_releases` et de l'affichage de l'audit |
| Triggers `stcm_audit_update` / `stcm_audit_delete` (06, 07) | Audit ensembliste (FOR EACH STATEMENT + tables de transition) remplaçant `stcm_audit` ; même contenu d'`audit_log` |

### Invariants (ne jamais les contourner)
- **Modèle snapshot** : chaque release contient sa copie complète des mappings.
- Clé métier d'un mapping dans une release :
  `(release_id, source_vocabulary_id, source_code, target_concept_id, relationship_id)`.
- Seule une release au statut `open` est modifiable ; le trigger `stcm_guard` l'impose.
  L'application vérifie aussi le statut pour afficher un message clair au lieu d'une erreur SQL.
- Une seule staging `open` à la fois (index unique partiel).
- **Statuts et cycle** : tout ce qu'un import ajoute ou modifie est `UNCHECKED` (seuls `IGNORED` /
  `FLAGGED` lus dans le fichier sont conservés) ; à la publication, les `UNCHECKED` deviennent `APPROVED`
  et la publication est refusée s'il reste des `FLAGGED`. Une release publiée est donc entièrement validée ;
  l'interface ne met en évidence que les statuts restant à traiter.
- **Imports** : annulables (`rollback_import`) tant que la release est ouverte et que les lignes n'ont pas
  été modifiées depuis ; affichés « archivés » une fois la release publiée ou archivée. Un import jamais
  chargé (en attente, en échec) peut être supprimé.
- Cycle : `v1.0 published` → `create_release('v1.1','staging','v1.0')` → imports / corrections →
  `promote_staging('v1.1','v2.0')` → `publish_release('v2.0', '<build CDM>')`.
- Pas de clé étrangère vers `vocab.concept` : le vocabulaire est rechargeable et
  `target_concept_id = 0` est autorisé (non mappé). La cohérence passe par `v_mapping_quality`.
- L'utilisateur courant est transmis à PostgreSQL à chaque transaction d'écriture via
  `SELECT set_config('app.user', :user, true)` pour alimenter `audit_log.changed_by`.

## 6. Reproduction de l'ergonomie Athena

On reproduit **la structure et les interactions** d'Athena, pas son code, son logo ni son nom.
Nom affiché de l'application : « Référentiel mappings OMOP — AP-HM ».

### Correspondance des écrans

| Athena | Notre application |
|---|---|
| Page d'accueil avec champ de recherche centré | `/` : champ de recherche centré, sélecteur de release, chiffres clés de la release |
| Search terms : facettes à gauche, tableau de résultats paginé | `/search-terms/terms` : même disposition, appliquée à `source_to_concept_map` |
| Fiche concept : Details + Relationships | `/mappings/{source_vocabulary_id}/{source_code}` : Détails + Cibles + Historique + Audit |
| Download | Export CSV de la recherche courante |

### Page de recherche (`/search-terms/terms`)
- **Bandeau supérieur** sombre (bleu-gris) avec le nom de l'appli, la navigation principale
  (Recherche, Import, Releases, Export, Comparer), un menu **Avancé** (Comparaison Athena, Qualité,
  paramètres Base Athena et Colonnes personnalisées, API) et le **sélecteur de release** à droite
  (par défaut : dernière publiée).
- **Barre de recherche** pleine largeur sous le bandeau : texte libre multi-mots (chaque mot, dans
  n'importe quel ordre, doit apparaître dans le code, le libellé source ou le libellé / l'ID du concept
  cible ; casse et accents ignorés), portée « Tout / Codes source / Concepts cibles », tri par pertinence
  (code ou ID exact d'abord), mots trouvés surlignés, suggestions instantanées sous la barre.
- **Recherche inverse** : un clic sur une cible filtre la recherche sur ce concept
  (`target_concept=`) et liste tous les codes source qui y sont mappés.
- **Rapidité** : la recherche est calculée une fois par requête dans `tmp_search` (table temporaire),
  puis comptage, page et facettes la lisent ; résultats mis en cache (release figée : définitif ;
  release ouverte : empreinte des données) et page par défaut précalculée au démarrage.
- La rubrique **Qualité** (facette, colonne, chiffre clé, menu) est masquée tant que `SHOW_QUALITY=false`.
- **Panneau de facettes à gauche** (≈ 25 % de largeur), chaque facette repliable, avec une case à
  cocher par valeur et le **nombre de résultats** à côté, triées par effectif décroissant :
  Vocabulaire source, Domaine, Vocabulaire cible, Statut (`mapping_status`), Équivalence,
  Relation (`Maps to` / `Maps to value`), Qualité (`quality_flag`), Validité (valide / invalide).
  Les compteurs se recalculent selon les autres filtres actifs (comportement Athena).
- **Filtres actifs** affichés en pastilles au-dessus du tableau, avec « Effacer » par pastille et
  « Tout effacer ».
- **Tableau de résultats** dense, une ligne par mapping, colonnes :
  Code source · Description · Vocab. source · Domaine · ID cible · Libellé cible · Vocab. cible ·
  Statut · Qualité. Libellé cible en italique grisé si le concept est introuvable.
  Clic sur une ligne → fiche du code source. En-têtes triables.
- **Pagination** en bas : taille de page 15 / 30 / 50 / 100 (défaut 30), total de résultats affiché.
- **État dans l'URL** (comme Athena) : `?release=v1.1&query=hb&source_vocabulary=APHM_LABO&domain=Measurement&page=2&page_size=30&sort=source_code&order=asc`.
  Facettes multi-valeurs répétées (`&domain=Measurement&domain=Observation`). Toute URL est
  partageable et rejouable. HTMX met à jour le tableau et les facettes avec `hx-push-url="true"`.
- Bouton **Exporter CSV** : exporte tous les résultats filtrés (streaming, pas seulement la page).

### Fiche d'un code source (`/mappings/{source_vocabulary_id}/{source_code}?release=…`)
- En-tête : `source_code` en titre, description en sous-titre, badges vocabulaire / domaine / statut.
- Onglets Bootstrap :
  1. **Détails** : carte clé/valeur de toutes les colonnes CDM, des extensions et des colonnes
     personnalisées (libellés issus de `custom_column`), comme la carte « Details » d'Athena.
  2. **Cibles** : tableau type « Relationships » d'Athena : Relation · ID cible · Libellé ·
     Domaine · Vocabulaire · Classe · Standard · Validité · Qualité.
  3. **Historique** : présence et valeurs du code dans chaque release (une colonne par release),
     changements surlignés.
  4. **Audit** : entrées de `audit_log` pour ce code (qui, quand, avant / après).
- Si la release est `open` : bouton **Modifier** (formulaire en fragment HTMX) avec le
  **sélecteur de concept cible** (recherche dans `vocab.concept`, standards valides par défaut,
  filtrable par domaine) et changement de statut / équivalence / commentaire.

### Style
- Bootstrap 5 par défaut, surcharges limitées dans `app/static/css/app.css` (variables CSS).
- Tableaux `table-sm table-hover`, police système, densité élevée comme Athena.
- Couleurs des statuts : APPROVED vert, UNCHECKED gris, FLAGGED orange, IGNORED gris barré ;
  qualité : OK vert, autre rouge. Toujours doubler la couleur d'un libellé texte.

## 7. Routes

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/` | Accueil, recherche centrée, chiffres clés |
| GET | `/search-terms/terms` | Page de recherche (page complète ou fragment si `HX-Request`) |
| GET | `/search-terms/terms/export.csv` | Export CSV de la recherche courante |
| GET | `/search-terms/suggest?query=&scope=&release=` | Suggestions instantanées (codes source, concepts cibles) pendant la saisie (fragment) |
| GET | `/mappings/{source_vocabulary_id}/{source_code}` | Fiche code source |
| GET/POST | `/mappings/{stcm_id}/edit` | Formulaire de correction (fragment) |
| GET | `/vocab/concepts/lookup` | Sélecteur de concept cible (fragment, `?q=&domain=`) |
| GET | `/imports` | Liste des imports |
| GET/POST | `/imports/new` | Étape 1 : upload |
| GET/POST | `/imports/{batch_id}/columns` | Étape 2 : correspondance des colonnes et valeurs par défaut |
| GET/POST | `/imports/{batch_id}/validate` | Étape 3 : validation et chargement |
| GET | `/imports/{batch_id}` | Rapport d'import et erreurs |
| GET | `/imports/{batch_id}/errors.csv` | Export CSV des rejets |
| POST | `/imports/{batch_id}/rollback` | Annuler un import chargé (release ouverte) |
| POST | `/imports/{batch_id}/delete` | Supprimer un import jamais chargé (en attente, en échec) |
| GET | `/releases` | Liste et lignée des releases |
| POST | `/releases/staging` | Créer la staging depuis la dernière publiée |
| POST | `/releases/{label}/promote` | Promouvoir la staging en majeure |
| POST | `/releases/{label}/publish` | Publier (saisie de la référence du build CDM) |
| POST | `/releases/initial` | Créer la première release majeure (référentiel vide uniquement) |
| GET | `/releases/diff?from=v1.0&to=v1.1` | Diff à facettes (type de changement, vocab. source) |
| GET | `/releases/diff/export.csv?from=…&to=…` | Export CSV du diff filtré |
| GET | `/compare?from=v1.0&to=v2.0` | Onglet Comparer : tableau de bord et liste par code (facettes `change_kind`, `source_vocabulary`, `field`) |
| GET | `/compare/export.csv` | Export CSV de la comparaison filtrée |
| GET | `/athena?release=v2.0` | Onglet Athena : comparaison aux « Maps to » natifs (facettes `status`, `source_vocabulary`, `mapping_status`) |
| GET | `/athena/export.csv` | Export CSV de la comparaison Athena filtrée |
| GET | `/settings/athena` | Paramétrage : connexions, correspondance des vocabulaires, synchronisation |
| POST | `/settings/athena/connections` | Enregistrer une connexion |
| POST | `/settings/athena/connections/{id}/{test,activate,delete}` | Tester / activer / supprimer une connexion |
| POST | `/settings/athena/vocabulary-map` | Ajouter ou modifier une correspondance de vocabulaire |
| POST | `/settings/athena/vocabulary-map/{source_vocabulary_id}/delete` | Supprimer une correspondance |
| POST | `/settings/athena/sync` | Synchroniser la copie locale depuis la base active |
| GET | `/quality` | Synthèse `v_mapping_quality` par release / vocabulaire |
| GET/POST | `/settings/custom-columns` | Gestion des colonnes personnalisées |
| GET | `/api/releases/{label}/source_to_concept_map.csv` | Export CDM strict pour le pipeline |
| GET | `/api/releases` | Liste JSON des releases |
| GET | `/api/releases/{label}/properties.zip` | Fichiers `<Domaine>.properties` (IGNORED et cibles 0 exclus) |
| GET | `/api` | Documentation locale de l'API (Swagger UI désactivé : il dépend d'un CDN) |
| GET | `/export?release=&format=&source_vocabulary=&mapping_status=&include_unmapped=` | Onglet Export : choix du format et du contenu |
| GET | `/export/download?…` | Téléchargement (ZIP properties, CSV CDM, CSV complet) |
| POST | `/export/write` | Écrit les `.properties` dans `EXPORT_DIR/PROPERTIES_SUBDIR` (défaut `data/30_properties`) |

Les URL sont en kebab-case, les paramètres de requête en snake_case.

## 8. Import CSV / Excel

Un import cible **toujours la release `open`** (staging, ou majeure non encore publiée).

1. **Upload** : `.csv`, `.txt`, `.xlsx`. Détection de l'encodage (UTF-8, puis CP1252) et du
   séparateur (`;`, `,`, tabulation) ; choix de la feuille pour Excel. Calcul du SHA-256 et
   avertissement si le même fichier a déjà été importé dans la release.
2. **Correspondance des colonnes** : aperçu des 20 premières lignes. Pour **chaque colonne de
   `source_to_concept_map`** (CDM, extensions, colonnes personnalisées), l'utilisateur choisit :
   - une colonne du fichier,
   - **ou une valeur par défaut appliquée à toute la colonne** (ex. `source_vocabulary_id = APHM_LABO`),
   - ou rien si la colonne est facultative.

   Pré-remplissage automatique par nom de colonne (insensible à la casse et aux accents) et à partir
   du dernier import du même `source_vocabulary_id`. Choix du domaine et du mode de chargement :
   - `insert` : rejette les lignes dont la clé existe déjà ;
   - `upsert` : met à jour les lignes existantes ;
   - `replace_vocabulary` : supprime puis recharge tout le `source_vocabulary_id` dans la release.

   Les choix sont enregistrés dans `import_batch.column_mapping` et `default_values`.
3. **Validation et chargement**, dans une seule transaction :
   - lecture par blocs (pandas `chunksize` / openpyxl `read_only`) ;
   - `COPY` vers une table temporaire `tmp_import` ;
   - contrôles en SQL : colonnes obligatoires, longueurs, dates, valeurs autorisées,
     `target_concept_id` présent dans `vocab.concept`, `target_vocabulary_id` cohérent ;
   - lignes invalides → `mapping.import_error` (n° de ligne, ligne brute, message) ;
   - lignes valides → `INSERT … ON CONFLICT` selon le mode ;
   - mise à jour des compteurs et du statut de `import_batch`.
4. **Rapport** : lus / chargés / rejetés, rejets téléchargeables en CSV, lien vers la recherche
   filtrée sur les lignes de l'import.

Objectif de performance : 300 000 lignes chargées en moins d'une minute.

## 9. Conventions de nommage

### SQL
- Identifiants en `snake_case`, minuscules, jamais de guillemets.
- Tables reprenant OMOP : noms OMOP exacts (`source_to_concept_map`, `concept`). Autres tables : nom
  au singulier (`release`, `import_batch`).
- Préfixes : `pk_` clé primaire, `fk_` clé étrangère, `uq_` contrainte d'unicité, `idx_` index,
  `ck_` contrainte de vérification, `v_` vue, `trg_` fonction de trigger.
- Fonctions : verbe à l'infinitif anglais (`create_release`, `diff_releases`), paramètres préfixés
  `p_`, variables locales préfixées `v_`.
- Mots-clés SQL en MAJUSCULES, une clause par ligne, alignement des colonnes dans les `SELECT`.
- Fichiers de migration : `NN_description_en_snake_case.sql`, numérotation sur deux chiffres
  continue (`04_add_mapping_tags.sql`). Chaque fichier est idempotent (`IF NOT EXISTS`,
  `CREATE OR REPLACE`) et commence par un en-tête de commentaire décrivant son but.

### Python
- PEP 8 ; modules et fonctions en `snake_case`, classes en `PascalCase`, constantes en `UPPER_SNAKE`.
- Suffixes de modules : `*_repo.py`, `*_service.py` ; routers nommés par ressource (`imports.py`).
- Modèles SQLAlchemy nommés comme la table au singulier en PascalCase : `Release`,
  `SourceToConceptMap`, `ImportBatch`, `CustomColumn`.
- Schémas pydantic suffixés : `*In` (entrée formulaire), `*Out` (sortie), `*Filter` (filtres).
- Identifiants (variables, fonctions, classes) **en anglais** ; textes de l'interface, messages
  d'erreur affichés et commentaires **en français**.

### Templates et front
- Pages : `templates/pages/<ressource>_<vue>.html` (ex. `search_results.html`, `mapping_detail.html`).
- Fragments HTMX : `templates/partials/_<nom>.html` (ex. `_facets.html`, `_results_table.html`).
- Identifiants HTML et classes CSS propres à l'appli en kebab-case, préfixées `ref-`
  (ex. `ref-facet-panel`).
- Chaque fragment HTMX a une cible stable (`id`) documentée en commentaire Jinja en tête de fichier.

### Git
- Commits au format `type(portée): message` en français, types `feat`, `fix`, `refactor`, `db`,
  `test`, `docs`, `chore` (ex. `db(mapping): ajout de la colonne tags`).
- Toute migration SQL fait l'objet d'un commit `db(...)` séparé.

## 10. Conventions de codage

### Général
- Annotations de type partout ; `ruff check` et `ruff format` sans erreur ; `mypy` sans erreur.
- Fonctions courtes (≈ 40 lignes max), un module = une responsabilité.
- Pas de code mort, pas de `print` (module `logging`, logger par module).
- Configuration uniquement via `app/config.py` (variables d'environnement) ; jamais d'identifiants
  ni de chemin en dur.

### Base de données
- **Tout paramètre SQL est lié** (`:param` ou expressions SQLAlchemy). Jamais de f-string ni de
  concaténation dans une requête. Les noms de colonnes dynamiques (tri, facettes, colonnes
  personnalisées) passent par une **liste blanche** définie dans le repository.
- Les transactions sont ouvertes dans les **services** (`with session.begin():`), jamais dans les
  routes ni dans les repositories.
- Les appels aux fonctions PostgreSQL passent par le repository concerné (`release_repo.promote_staging()`).
- Base Athena distante : connexion psycopg en lecture seule (`default_transaction_read_only`). Le nom
  de schéma distant est le seul identifiant dynamique : validé (CHECK SQL + motif) et cité par
  `psycopg.sql.Identifier` ; toutes les valeurs restent des paramètres liés.
- Les colonnes personnalisées sont lues et écrites via `extra` (`extra ->> 'nom'`), avec
  validation selon `custom_column.data_type` et `allowed_values` dans le service.
- Toute requête de recherche est paginée (`LIMIT/OFFSET`) et filtrée par `release_id`.
- Requête de facettes : une CTE de base filtrée, puis un `GROUP BY` par facette, chaque facette
  excluant son propre filtre pour des compteurs à la Athena.

### FastAPI / HTMX
- Une route renvoie la page complète, ou seulement le fragment si l'en-tête `HX-Request` est présent.
- Formulaires validés par pydantic ; erreurs réaffichées dans le fragment avec `is-invalid`.
- Les actions irréversibles (publier, promouvoir, `replace_vocabulary`) demandent une confirmation
  (`hx-confirm`) et rappellent l'impact (nombre de lignes, release concernée).
- JavaScript limité à `app/static/js/app.js` (petites interactions) ; pas de logique métier côté client.

### Tests
- `pytest` sur une base PostgreSQL de test (`TEST_DATABASE_URL`), schéma recréé à partir de
  `db/ddl/` à chaque session de tests.
- Couverture minimale : fonctions SQL (cycle de release, diff, verrouillage, audit), import (les trois
  modes, valeurs par défaut, rejets), recherche à facettes (compteurs), routes principales (statut 200).
- Une correction de bug = un test qui reproduit le bug.

## 11. Migrations

- `scripts/migrate.py` applique dans l'ordre les fichiers `db/ddl/*.sql` absents de la table
  `public.schema_migration (version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())`,
  qu'il crée si besoin. Chaque fichier est appliqué dans sa propre transaction.
- **Un fichier déjà appliqué n'est jamais modifié** : le script refuse de continuer si le
  checksum d'un fichier appliqué a changé. Toute évolution = nouveau fichier numéroté.
- `02_vocab_indexes.sql` est rejouable après chaque rechargement du vocabulaire.

## 12. Commandes

```bash
# Installation
python -m venv .venv && source .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # DATABASE_URL=postgresql+psycopg://user:pwd@host:5432/omop_referentiel

# Base
python scripts/migrate.py                               # applique db/ddl/
python scripts/load_vocab.py --dir /chemin/athena       # charge CONCEPT.csv et VOCABULARY.csv puis rejoue 02
python scripts/load_usagi_dir.py --dir data --publish <build> --staging v1.1  # exports Usagi -> v1.0
python scripts/export_release.py --release v2.0 --format properties             # -> data/30_properties

# Environnement isolé Windows (sans Internet)
powershell -ExecutionPolicy Bypass -File scripts\install.ps1    # .venv depuis wheels/
powershell -ExecutionPolicy Bypass -File scripts\start.ps1      # ou double-clic sur demarrer.cmd

# Lancement
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Qualité
ruff check . && ruff format --check . && mypy app scripts tests
pytest -q
```

## 13. Feuille de route

1. **Socle** : config, connexion, `migrate.py`, `load_vocab.py`, layout `base.html`, assets locaux.
2. **Recherche Athena** : page de recherche, facettes avec compteurs, pagination, URL partageables, export CSV.
3. **Fiche code source** : onglets Détails, Cibles, Historique, Audit.
4. **Releases** : liste, création de la staging, promotion, publication, diff + export.
5. **Import** : assistant en trois étapes, modes, rapport d'erreurs.
6. **Édition** : correction manuelle avec sélecteur de concept cible.
7. **Qualité et colonnes personnalisées**, API pour le pipeline.

Livrer chaque lot testé et fonctionnel avant de passer au suivant.

## 14. Règles pour Claude

- Lire ce fichier et les DDL de `db/ddl/` avant de coder.
- Ne jamais modifier un fichier de `db/ddl/` déjà appliqué : créer une nouvelle migration et
  mettre à jour la section 5 et l'annexe de ce fichier.
- Ne pas déplacer de logique de versionnement de PostgreSQL vers Python.
- Ne pas exposer le vocabulaire comme un navigateur de concepts ; ne pas ajouter d'authentification.
- Ne jamais utiliser de CDN ni d'appel réseau externe.
- Livrer des fichiers complets, pas des extraits partiels.
- Lancer `ruff`, `mypy` et `pytest` avant de considérer une tâche terminée ; signaler tout échec.
- En cas d'ambiguïté sur une règle métier (cycle de release, mode d'import, qualité), demander
  avant d'implémenter.

---

## Annexe — DDL de référence

Copie de `db/ddl/` au moment de la rédaction. Les fichiers de `db/ddl/` font foi ; maintenir
cette annexe synchronisée à chaque nouvelle migration.

### `db/ddl/01_vocab_tables.sql`

```sql
-- =====================================================================
-- 01 — Tables de vocabulaire (schéma vocab) — lecture seule pour l'appli
-- Base : omop_referentiel
-- Le vocabulaire n'est PAS navigable dans l'application : il sert uniquement à
--   * afficher libellé / domaine / statut standard des concepts cibles,
--   * valider les cibles à l'import et alimenter le contrôle qualité,
--   * proposer des concepts cibles lors d'une correction manuelle.
-- Seules CONCEPT et VOCABULARY (format OMOP CDM v5.4) sont nécessaires.
-- Chargement : CSV Athena (tabulés, dates YYYYMMDD) par \copy, puis 02_vocab_indexes.sql.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS vocab;

CREATE TABLE IF NOT EXISTS vocab.concept (
    concept_id        integer      NOT NULL,
    concept_name      varchar(255) NOT NULL,
    domain_id         varchar(20)  NOT NULL,
    vocabulary_id     varchar(20)  NOT NULL,
    concept_class_id  varchar(20)  NOT NULL,
    standard_concept  varchar(1),
    concept_code      varchar(50)  NOT NULL,
    valid_start_date  date         NOT NULL,
    valid_end_date    date         NOT NULL,
    invalid_reason    varchar(1),
    CONSTRAINT pk_concept PRIMARY KEY (concept_id)
);

CREATE TABLE IF NOT EXISTS vocab.vocabulary (
    vocabulary_id          varchar(20)  NOT NULL,
    vocabulary_name        varchar(255) NOT NULL,
    vocabulary_reference   varchar(255),
    vocabulary_version     varchar(255),
    vocabulary_concept_id  integer      NOT NULL,
    CONSTRAINT pk_vocabulary PRIMARY KEY (vocabulary_id)
);

-- Chargement (psql) :
-- \copy vocab.concept    FROM 'CONCEPT.csv'    WITH (FORMAT csv, DELIMITER E'\t', HEADER true, QUOTE E'\b')
-- \copy vocab.vocabulary FROM 'VOCABULARY.csv' WITH (FORMAT csv, DELIMITER E'\t', HEADER true, QUOTE E'\b')
-- Version chargée : SELECT vocabulary_version FROM vocab.vocabulary WHERE vocabulary_id = 'None';
```

### `db/ddl/02_vocab_indexes.sql`

```sql
-- =====================================================================
-- 02 — Index du vocabulaire
-- À exécuter APRÈS le chargement des CSV Athena.
-- Rechargement complet : DROP de ces index, \copy, relance de ce script.
-- =====================================================================

-- Validation à l'import : retrouver un concept par (vocabulaire, code)
CREATE INDEX IF NOT EXISTS idx_concept_vocab_code ON vocab.concept (vocabulary_id, concept_code);

-- Sélecteur de concept cible (correction manuelle) : recherche par libellé ou code,
-- filtrée sur les concepts standards valides
CREATE INDEX IF NOT EXISTS idx_concept_name_trgm  ON vocab.concept USING gin (concept_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_concept_code_trgm  ON vocab.concept USING gin (concept_code gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_concept_std_domain ON vocab.concept (standard_concept, domain_id);

ANALYZE vocab.concept;
ANALYZE vocab.vocabulary;
```

### `db/ddl/03_mapping.sql`

```sql
-- =====================================================================
-- 03 — Référentiel de mappings versionné (schéma mapping)
-- Prérequis : 01_vocab_tables.sql
-- Cycle de vie : v1.0 (published) -> v1.1 (staging, open) -> v2.0 (published) ...
-- Modèle "snapshot" : chaque release possède sa copie complète des lignes.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS mapping;

-- ---------------------------------------------------------------------
-- 2. Releases
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mapping.release (
    release_id          serial PRIMARY KEY,
    label               text NOT NULL UNIQUE,                 -- 'v1.0', 'v1.1', 'v2.0'
    kind                text NOT NULL CHECK (kind IN ('major', 'staging')),
    status              text NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'frozen', 'published', 'archived')),
    parent_release_id   int REFERENCES mapping.release (release_id),
    vocabulary_version  text,                                 -- vocab.vocabulary 'None'.vocabulary_version
    cdm_build_ref       text,                                 -- identifiant du CDM généré avec cette release
    description         text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    frozen_at           timestamptz,
    published_at        timestamptz
);

-- Une seule staging ouverte à la fois
CREATE UNIQUE INDEX IF NOT EXISTS uq_release_one_open_staging
    ON mapping.release (kind) WHERE kind = 'staging' AND status = 'open';

-- ---------------------------------------------------------------------
-- 3. Colonnes personnalisées (stockées dans source_to_concept_map.extra)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mapping.custom_column (
    column_name     text PRIMARY KEY CHECK (column_name ~ '^[a-z][a-z0-9_]*$'),
    label           text NOT NULL,
    data_type       text NOT NULL DEFAULT 'text'
                    CHECK (data_type IN ('text', 'integer', 'numeric', 'date', 'boolean')),
    allowed_values  text[],                                   -- liste fermée optionnelle
    is_required     boolean NOT NULL DEFAULT false,
    description     text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 4. Imports CSV / Excel
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mapping.import_batch (
    import_batch_id       serial PRIMARY KEY,
    release_id            int NOT NULL REFERENCES mapping.release (release_id),
    file_name             text NOT NULL,
    file_sha256           text,
    sheet_name            text,
    domain_id             varchar(20),
    source_vocabulary_id  varchar(20),
    column_mapping        jsonb NOT NULL DEFAULT '{}',  -- {"source_code": "CODE_LOCAL", ...}
    default_values        jsonb NOT NULL DEFAULT '{}',  -- {"source_vocabulary_id": "APHM_LABO", ...}
    load_mode             text NOT NULL DEFAULT 'upsert'
                          CHECK (load_mode IN ('insert', 'upsert', 'replace_vocabulary')),
    rows_read             int NOT NULL DEFAULT 0,
    rows_loaded           int NOT NULL DEFAULT 0,
    rows_rejected         int NOT NULL DEFAULT 0,
    status                text NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'loaded', 'partial', 'failed', 'rolled_back')),
    created_by            text,
    created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mapping.import_error (
    import_error_id  bigserial PRIMARY KEY,
    import_batch_id  int NOT NULL REFERENCES mapping.import_batch (import_batch_id) ON DELETE CASCADE,
    row_number       int,
    raw_row          jsonb,
    error_message    text NOT NULL
);

-- ---------------------------------------------------------------------
-- 5. SOURCE_TO_CONCEPT_MAP versionnée et étendue
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mapping.source_to_concept_map (
    stcm_id                  bigserial PRIMARY KEY,
    release_id               int NOT NULL REFERENCES mapping.release (release_id),
    -- colonnes OMOP CDM v5.4
    source_code              varchar(50)  NOT NULL,
    source_concept_id        int          NOT NULL DEFAULT 0,
    source_vocabulary_id     varchar(20)  NOT NULL,
    source_code_description  varchar(255),
    target_concept_id        int          NOT NULL,
    target_vocabulary_id     varchar(20)  NOT NULL,
    valid_start_date         date         NOT NULL DEFAULT DATE '1970-01-01',
    valid_end_date           date         NOT NULL DEFAULT DATE '2099-12-31',
    invalid_reason           varchar(1),
    -- extensions AP-HM
    domain_id                varchar(20),
    relationship_id          varchar(20)  NOT NULL DEFAULT 'Maps to',   -- 'Maps to' / 'Maps to value'
    source_frequency         int,
    mapping_status           text NOT NULL DEFAULT 'UNCHECKED'
                             CHECK (mapping_status IN ('APPROVED', 'UNCHECKED', 'FLAGGED', 'IGNORED')),
    equivalence              text
                             CHECK (equivalence IN ('EQUAL', 'EQUIVALENT', 'WIDER', 'NARROWER', 'INEXACT', 'UNMATCHED')),
    mapping_comment          text,
    mapped_by                text,
    reviewed_by              text,
    reviewed_at              timestamptz,
    import_batch_id          int REFERENCES mapping.import_batch (import_batch_id),
    extra                    jsonb NOT NULL DEFAULT '{}',               -- colonnes personnalisées
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_stcm_release_key
        UNIQUE (release_id, source_vocabulary_id, source_code, target_concept_id, relationship_id)
);

CREATE INDEX IF NOT EXISTS idx_stcm_release_src    ON mapping.source_to_concept_map (release_id, source_vocabulary_id, source_code);
CREATE INDEX IF NOT EXISTS idx_stcm_release_target ON mapping.source_to_concept_map (release_id, target_concept_id);
CREATE INDEX IF NOT EXISTS idx_stcm_release_domain ON mapping.source_to_concept_map (release_id, domain_id);
CREATE INDEX IF NOT EXISTS idx_stcm_target         ON mapping.source_to_concept_map (target_concept_id);
CREATE INDEX IF NOT EXISTS idx_stcm_code_trgm      ON mapping.source_to_concept_map USING gin (source_code gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_stcm_desc_trgm      ON mapping.source_to_concept_map USING gin (source_code_description gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_stcm_extra          ON mapping.source_to_concept_map USING gin (extra);

-- ---------------------------------------------------------------------
-- 6. Audit des modifications (utile surtout pour la staging)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mapping.audit_log (
    audit_id     bigserial PRIMARY KEY,
    release_id   int,
    stcm_id      bigint,
    operation    text NOT NULL,
    old_row      jsonb,
    new_row      jsonb,
    changed_by   text NOT NULL DEFAULT coalesce(current_setting('app.user', true), current_user),
    changed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_release ON mapping.audit_log (release_id, changed_at);
CREATE INDEX IF NOT EXISTS idx_audit_stcm    ON mapping.audit_log (stcm_id);

-- ---------------------------------------------------------------------
-- 7. Triggers : verrouillage des releases figées + audit + updated_at
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mapping.trg_stcm_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT status INTO v_status FROM mapping.release WHERE release_id = OLD.release_id;
        IF v_status <> 'open' THEN
            RAISE EXCEPTION 'Release % non modifiable (statut %)', OLD.release_id, v_status;
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT status INTO v_status FROM mapping.release WHERE release_id = NEW.release_id;
        IF v_status <> 'open' THEN
            RAISE EXCEPTION 'Release % non modifiable (statut %)', NEW.release_id, v_status;
        END IF;
        IF TG_OP = 'UPDATE' THEN
            NEW.updated_at := now();
        END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;
END $$;

DROP TRIGGER IF EXISTS stcm_guard ON mapping.source_to_concept_map;
CREATE TRIGGER stcm_guard
    BEFORE INSERT OR UPDATE OR DELETE ON mapping.source_to_concept_map
    FOR EACH ROW EXECUTE FUNCTION mapping.trg_stcm_guard();

-- Audit ligne à ligne sur UPDATE/DELETE uniquement
-- (les INSERT massifs sont tracés par import_batch, pas ligne à ligne)
CREATE OR REPLACE FUNCTION mapping.trg_stcm_audit()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO mapping.audit_log (release_id, stcm_id, operation, old_row, new_row)
    VALUES (
        OLD.release_id,
        OLD.stcm_id,
        TG_OP,
        to_jsonb(OLD),
        CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(NEW) END
    );
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS stcm_audit ON mapping.source_to_concept_map;
CREATE TRIGGER stcm_audit
    AFTER UPDATE OR DELETE ON mapping.source_to_concept_map
    FOR EACH ROW EXECUTE FUNCTION mapping.trg_stcm_audit();

-- ---------------------------------------------------------------------
-- 8. Gestion des releases
-- ---------------------------------------------------------------------

-- Crée une release en copiant intégralement la release parente
CREATE OR REPLACE FUNCTION mapping.create_release(
    p_label        text,
    p_kind         text,
    p_parent_label text DEFAULT NULL,
    p_description  text DEFAULT NULL
) RETURNS int LANGUAGE plpgsql AS $$
DECLARE
    v_parent int;
    v_new    int;
BEGIN
    IF p_parent_label IS NOT NULL THEN
        SELECT release_id INTO v_parent FROM mapping.release WHERE label = p_parent_label;
        IF v_parent IS NULL THEN
            RAISE EXCEPTION 'Release parente % introuvable', p_parent_label;
        END IF;
    END IF;

    INSERT INTO mapping.release (label, kind, status, parent_release_id, description, vocabulary_version)
    VALUES (
        p_label, p_kind, 'open', v_parent, p_description,
        (SELECT vocabulary_version FROM mapping.release WHERE release_id = v_parent)
    )
    RETURNING release_id INTO v_new;

    IF v_parent IS NOT NULL THEN
        INSERT INTO mapping.source_to_concept_map (
            release_id, source_code, source_concept_id, source_vocabulary_id, source_code_description,
            target_concept_id, target_vocabulary_id, valid_start_date, valid_end_date, invalid_reason,
            domain_id, relationship_id, source_frequency, mapping_status, equivalence, mapping_comment,
            mapped_by, reviewed_by, reviewed_at, import_batch_id, extra, created_at
        )
        SELECT
            v_new, source_code, source_concept_id, source_vocabulary_id, source_code_description,
            target_concept_id, target_vocabulary_id, valid_start_date, valid_end_date, invalid_reason,
            domain_id, relationship_id, source_frequency, mapping_status, equivalence, mapping_comment,
            mapped_by, reviewed_by, reviewed_at, import_batch_id, extra, created_at
        FROM mapping.source_to_concept_map
        WHERE release_id = v_parent;
    END IF;

    RETURN v_new;
END $$;

-- Fige puis publie une release (rattachée à un build CDM)
CREATE OR REPLACE FUNCTION mapping.publish_release(p_label text, p_cdm_build_ref text DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    UPDATE mapping.release
       SET status        = 'published',
           frozen_at     = coalesce(frozen_at, now()),
           published_at  = now(),
           cdm_build_ref = coalesce(p_cdm_build_ref, cdm_build_ref)
     WHERE label = p_label AND status IN ('open', 'frozen');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Release % introuvable ou déjà publiée/archivée', p_label;
    END IF;
END $$;

-- Promotion : staging (v1.1) -> nouvelle majeure (v2.0) ; la staging est archivée
CREATE OR REPLACE FUNCTION mapping.promote_staging(p_staging_label text, p_new_label text)
RETURNS int LANGUAGE plpgsql AS $$
DECLARE
    v_new int;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mapping.release
         WHERE label = p_staging_label AND kind = 'staging' AND status = 'open'
    ) THEN
        RAISE EXCEPTION 'Staging ouverte % introuvable', p_staging_label;
    END IF;

    v_new := mapping.create_release(p_new_label, 'major', p_staging_label,
                                    'Promotion de ' || p_staging_label);

    UPDATE mapping.release
       SET status = 'archived', frozen_at = now()
     WHERE label = p_staging_label;

    RETURN v_new;
END $$;

-- ---------------------------------------------------------------------
-- 9. Diff entre deux releases
--    Clé métier : (source_vocabulary_id, source_code, target_concept_id, relationship_id)
--    Un changement de cible apparaît comme REMOVED + ADDED.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mapping.diff_releases(p_from text, p_to text)
RETURNS TABLE (
    change_type          text,
    source_vocabulary_id varchar,
    source_code          varchar,
    target_concept_id    int,
    relationship_id      varchar,
    changed_fields       text[],
    old_row              jsonb,
    new_row              jsonb
) LANGUAGE sql STABLE AS $$
    WITH excl AS (
        SELECT ARRAY['stcm_id', 'release_id', 'import_batch_id', 'created_at', 'updated_at'] AS cols
    ),
    a AS (
        SELECT s.source_vocabulary_id, s.source_code, s.target_concept_id, s.relationship_id,
               to_jsonb(s) - (SELECT cols FROM excl) AS j
          FROM mapping.source_to_concept_map s
          JOIN mapping.release r USING (release_id)
         WHERE r.label = p_from
    ),
    b AS (
        SELECT s.source_vocabulary_id, s.source_code, s.target_concept_id, s.relationship_id,
               to_jsonb(s) - (SELECT cols FROM excl) AS j
          FROM mapping.source_to_concept_map s
          JOIN mapping.release r USING (release_id)
         WHERE r.label = p_to
    )
    SELECT
        CASE WHEN a.j IS NULL THEN 'ADDED'
             WHEN b.j IS NULL THEN 'REMOVED'
             ELSE 'MODIFIED' END,
        coalesce(b.source_vocabulary_id, a.source_vocabulary_id),
        coalesce(b.source_code,          a.source_code),
        coalesce(b.target_concept_id,    a.target_concept_id),
        coalesce(b.relationship_id,      a.relationship_id),
        CASE WHEN a.j IS NOT NULL AND b.j IS NOT NULL THEN
            ARRAY(SELECT k FROM jsonb_object_keys(b.j) k
                   WHERE (a.j -> k) IS DISTINCT FROM (b.j -> k)
                   ORDER BY k)
        END,
        a.j,
        b.j
    FROM a
    FULL JOIN b
      ON  a.source_vocabulary_id = b.source_vocabulary_id
      AND a.source_code          = b.source_code
      AND a.target_concept_id    = b.target_concept_id
      AND a.relationship_id      = b.relationship_id
    WHERE a.j IS NULL OR b.j IS NULL OR a.j IS DISTINCT FROM b.j;
$$;

-- Lignée des releases et volumétrie
CREATE OR REPLACE VIEW mapping.v_release_lineage AS
SELECT r.label, r.kind, r.status, p.label AS parent_label, r.vocabulary_version,
       r.cdm_build_ref, r.created_at, r.published_at,
       (SELECT count(*) FROM mapping.source_to_concept_map s WHERE s.release_id = r.release_id) AS n_mappings
  FROM mapping.release r
  LEFT JOIN mapping.release p ON p.release_id = r.parent_release_id;

-- ---------------------------------------------------------------------
-- 10. Contrôle qualité des cibles (contre le vocabulaire chargé)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW mapping.v_mapping_quality AS
SELECT s.release_id, s.stcm_id, s.source_vocabulary_id, s.source_code, s.target_concept_id,
       CASE
           WHEN s.target_concept_id = 0           THEN 'UNMAPPED'
           WHEN c.concept_id IS NULL              THEN 'TARGET_NOT_FOUND'
           WHEN c.invalid_reason IS NOT NULL      THEN 'TARGET_INVALID'
           WHEN c.standard_concept IS DISTINCT FROM 'S' THEN 'TARGET_NOT_STANDARD'
           WHEN s.target_vocabulary_id <> c.vocabulary_id THEN 'VOCABULARY_MISMATCH'
           WHEN s.domain_id IS NOT NULL AND s.domain_id <> c.domain_id THEN 'DOMAIN_MISMATCH'
           ELSE 'OK'
       END AS quality_flag,
       c.concept_name, c.domain_id AS target_domain_id, c.standard_concept, c.invalid_reason
  FROM mapping.source_to_concept_map s
  LEFT JOIN vocab.concept c ON c.concept_id = s.target_concept_id;

-- ---------------------------------------------------------------------
-- 11. Vue consommée par le pipeline (format CDM v5.4 strict)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW mapping.v_stcm_published AS
SELECT s.source_code, s.source_concept_id, s.source_vocabulary_id, s.source_code_description,
       s.target_concept_id, s.target_vocabulary_id, s.valid_start_date, s.valid_end_date,
       s.invalid_reason
  FROM mapping.source_to_concept_map s
 WHERE s.release_id = (
        SELECT release_id FROM mapping.release
         WHERE status = 'published'
         ORDER BY published_at DESC
         LIMIT 1)
   AND s.mapping_status <> 'IGNORED';
```

### `db/ddl/04_import_helpers.sql`

```sql
-- =====================================================================
-- 04 — Fonctions de conversion tolérantes (contrôles d'import) et vue CDM par release
-- Prérequis : 03_mapping.sql
--   * mapping.try_cast_date(text)  : date ISO, YYYYMMDD ou JJ/MM/AAAA, NULL si invalide
--   * mapping.try_cast_int(text)   : entier 32 bits, NULL si invalide
--   * mapping.try_cast_numeric(text) : numérique (virgule décimale acceptée), NULL si invalide
--   * mapping.try_cast_boolean(text) : vrai/faux, oui/non, true/false, 1/0, NULL si invalide
--   * mapping.v_stcm_cdm : format CDM v5.4 strict pour n'importe quelle release (export pipeline)
-- =====================================================================

CREATE OR REPLACE FUNCTION mapping.try_cast_date(p_value text)
RETURNS date LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    v_value text := btrim(p_value);
BEGIN
    IF v_value IS NULL OR v_value = '' THEN
        RETURN NULL;
    END IF;
    IF v_value ~ '^\d{4}-\d{2}-\d{2}([ T].*)?$' THEN
        RETURN make_date(substr(v_value, 1, 4)::int, substr(v_value, 6, 2)::int, substr(v_value, 9, 2)::int);
    ELSIF v_value ~ '^\d{8}$' THEN
        RETURN make_date(substr(v_value, 1, 4)::int, substr(v_value, 5, 2)::int, substr(v_value, 7, 2)::int);
    ELSIF v_value ~ '^\d{2}/\d{2}/\d{4}$' THEN
        RETURN make_date(substr(v_value, 7, 4)::int, substr(v_value, 4, 2)::int, substr(v_value, 1, 2)::int);
    END IF;
    RETURN NULL;
EXCEPTION
    WHEN others THEN
        RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION mapping.try_cast_int(p_value text)
RETURNS int LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
               WHEN btrim(p_value) ~ '^-?\d{1,9}$' THEN btrim(p_value)::int
           END;
$$;

CREATE OR REPLACE FUNCTION mapping.try_cast_numeric(p_value text)
RETURNS numeric LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
               WHEN replace(btrim(p_value), ',', '.') ~ '^-?\d+(\.\d+)?$'
               THEN replace(btrim(p_value), ',', '.')::numeric
           END;
$$;

CREATE OR REPLACE FUNCTION mapping.try_cast_boolean(p_value text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
               WHEN lower(btrim(p_value)) IN ('true', 't', 'vrai', 'oui', 'o', 'yes', 'y', '1') THEN true
               WHEN lower(btrim(p_value)) IN ('false', 'f', 'faux', 'non', 'n', 'no', '0')    THEN false
           END;
$$;

-- Format CDM v5.4 strict, toutes releases (le pipeline filtre sur release_label)
CREATE OR REPLACE VIEW mapping.v_stcm_cdm AS
SELECT r.label                   AS release_label,
       s.source_code,
       s.source_concept_id,
       s.source_vocabulary_id,
       s.source_code_description,
       s.target_concept_id,
       s.target_vocabulary_id,
       s.valid_start_date,
       s.valid_end_date,
       s.invalid_reason
  FROM mapping.source_to_concept_map s
  JOIN mapping.release r ON r.release_id = s.release_id
 WHERE s.mapping_status <> 'IGNORED';
```

### `db/ddl/05_diff_releases_perf.sql`

```sql
-- =====================================================================
-- 05 — Optimisation de mapping.diff_releases (même signature, même résultat)
-- Prérequis : 03_mapping.sql
-- La version de 03 sérialise toutes les lignes des deux releases en jsonb avant de
-- les comparer. Celle-ci compare d'abord les colonnes, puis ne construit old_row /
-- new_row / changed_fields que pour les lignes ADDED / REMOVED / MODIFIED.
-- IMPORTANT : toute nouvelle colonne de source_to_concept_map doit être ajoutée à la
-- comparaison ci-dessous (nouvelle migration), sauf si elle est exclue du diff
-- (stcm_id, release_id, import_batch_id, created_at, updated_at).
-- =====================================================================

CREATE OR REPLACE FUNCTION mapping.diff_releases(p_from text, p_to text)
RETURNS TABLE (
    change_type          text,
    source_vocabulary_id varchar,
    source_code          varchar,
    target_concept_id    int,
    relationship_id      varchar,
    changed_fields       text[],
    old_row              jsonb,
    new_row              jsonb
) LANGUAGE sql STABLE AS $$
    WITH excl AS (
        SELECT ARRAY['stcm_id', 'release_id', 'import_batch_id', 'created_at', 'updated_at'] AS cols
    ),
    a AS (
        SELECT s.*
          FROM mapping.source_to_concept_map s
         WHERE s.release_id = (SELECT r.release_id FROM mapping.release r WHERE r.label = p_from)
    ),
    b AS (
        SELECT s.*
          FROM mapping.source_to_concept_map s
         WHERE s.release_id = (SELECT r.release_id FROM mapping.release r WHERE r.label = p_to)
    ),
    d AS (
        SELECT CASE WHEN a.stcm_id IS NULL THEN NULL ELSE to_jsonb(a) - (SELECT cols FROM excl) END AS aj,
               CASE WHEN b.stcm_id IS NULL THEN NULL ELSE to_jsonb(b) - (SELECT cols FROM excl) END AS bj,
               coalesce(b.source_vocabulary_id, a.source_vocabulary_id) AS source_vocabulary_id,
               coalesce(b.source_code,          a.source_code)          AS source_code,
               coalesce(b.target_concept_id,    a.target_concept_id)    AS target_concept_id,
               coalesce(b.relationship_id,      a.relationship_id)      AS relationship_id
          FROM a
          FULL JOIN b
            ON  a.source_vocabulary_id = b.source_vocabulary_id
            AND a.source_code          = b.source_code
            AND a.target_concept_id    = b.target_concept_id
            AND a.relationship_id      = b.relationship_id
         WHERE a.stcm_id IS NULL
            OR b.stcm_id IS NULL
            OR (a.source_concept_id, a.source_code_description, a.target_vocabulary_id, a.valid_start_date,
                a.valid_end_date, a.invalid_reason, a.domain_id, a.source_frequency, a.mapping_status,
                a.equivalence, a.mapping_comment, a.mapped_by, a.reviewed_by, a.reviewed_at, a.extra)
               IS DISTINCT FROM
               (b.source_concept_id, b.source_code_description, b.target_vocabulary_id, b.valid_start_date,
                b.valid_end_date, b.invalid_reason, b.domain_id, b.source_frequency, b.mapping_status,
                b.equivalence, b.mapping_comment, b.mapped_by, b.reviewed_by, b.reviewed_at, b.extra)
    )
    SELECT
        CASE WHEN d.aj IS NULL THEN 'ADDED'
             WHEN d.bj IS NULL THEN 'REMOVED'
             ELSE 'MODIFIED' END,
        d.source_vocabulary_id,
        d.source_code,
        d.target_concept_id,
        d.relationship_id,
        CASE WHEN d.aj IS NOT NULL AND d.bj IS NOT NULL THEN
            ARRAY(SELECT k FROM jsonb_object_keys(d.bj) k
                   WHERE (d.aj -> k) IS DISTINCT FROM (d.bj -> k)
                   ORDER BY k)
        END,
        d.aj,
        d.bj
    FROM d;
$$;
```

### `db/ddl/06_audit_statement_trigger.sql`

```sql
-- =====================================================================
-- 06 — Audit ensembliste : triggers FOR EACH STATEMENT avec tables de transition
-- Prérequis : 03_mapping.sql
-- Remplace le trigger ligne à ligne stcm_audit (03) par deux triggers d'instruction.
-- Le contenu de mapping.audit_log est identique (une ligne par mapping modifié ou
-- supprimé, old_row / new_row complets, changed_by issu de app.user), mais les
-- mises à jour massives (import upsert, replace_vocabulary) sont nettement plus rapides.
-- =====================================================================

DROP TRIGGER IF EXISTS stcm_audit ON mapping.source_to_concept_map;
DROP FUNCTION IF EXISTS mapping.trg_stcm_audit();

CREATE OR REPLACE FUNCTION mapping.trg_stcm_audit_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO mapping.audit_log (release_id, stcm_id, operation, old_row, new_row)
    SELECT o.release_id,
           o.stcm_id,
           'UPDATE',
           to_jsonb(o),
           to_jsonb(n)
      FROM old_rows o
      JOIN new_rows n ON n.stcm_id = o.stcm_id
     ORDER BY o.stcm_id;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION mapping.trg_stcm_audit_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO mapping.audit_log (release_id, stcm_id, operation, old_row, new_row)
    SELECT o.release_id,
           o.stcm_id,
           'DELETE',
           to_jsonb(o),
           NULL
      FROM old_rows o
     ORDER BY o.stcm_id;
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS stcm_audit_update ON mapping.source_to_concept_map;
CREATE TRIGGER stcm_audit_update
    AFTER UPDATE ON mapping.source_to_concept_map
    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
    FOR EACH STATEMENT EXECUTE FUNCTION mapping.trg_stcm_audit_update();

DROP TRIGGER IF EXISTS stcm_audit_delete ON mapping.source_to_concept_map;
CREATE TRIGGER stcm_audit_delete
    AFTER DELETE ON mapping.source_to_concept_map
    REFERENCING OLD TABLE AS old_rows
    FOR EACH STATEMENT EXECUTE FUNCTION mapping.trg_stcm_audit_delete();
```

### `db/ddl/07_audit_update_hash_join.sql`

```sql
-- =====================================================================
-- 07 — Audit des UPDATE : jointure par hachage des tables de transition
-- Prérequis : 06_audit_statement_trigger.sql
-- Les tables de transition old_rows / new_rows n'ont ni index ni statistiques :
-- sans consigne, le planificateur peut choisir une boucle imbriquée (coût quadratique
-- sur un import massif). La fonction désactive les boucles imbriquées pour sa seule
-- exécution, ce qui impose une jointure par hachage sur stcm_id.
-- =====================================================================

CREATE OR REPLACE FUNCTION mapping.trg_stcm_audit_update()
RETURNS trigger LANGUAGE plpgsql
SET enable_nestloop = off
AS $$
BEGIN
    INSERT INTO mapping.audit_log (release_id, stcm_id, operation, old_row, new_row)
    SELECT o.release_id,
           o.stcm_id,
           'UPDATE',
           to_jsonb(o),
           to_jsonb(n)
      FROM old_rows o
      JOIN new_rows n ON n.stcm_id = o.stcm_id
     ORDER BY o.stcm_id;
    RETURN NULL;
END $$;
```

### `db/ddl/08_diff_codes.sql`

```sql
-- =====================================================================
-- 08 — Comparaison de deux releases au niveau du code source
-- Prérequis : 05_diff_releases_perf.sql
-- mapping.diff_codes(p_from, p_to) regroupe le résultat de diff_releases par
-- (source_vocabulary_id, source_code) et qualifie le changement :
--   NEW_CODE       : code absent de p_from, présent dans p_to
--   REMOVED_CODE   : code présent dans p_from, absent de p_to
--   TARGET_CHANGED : cibles (target_concept_id, relationship_id) ajoutées ou retirées
--   MODIFIED       : mêmes cibles, autres colonnes modifiées (statut, commentaire…)
-- old_targets / new_targets : cibles du code dans chaque release (jsonb).
-- =====================================================================

CREATE OR REPLACE FUNCTION mapping.diff_codes(p_from text, p_to text)
RETURNS TABLE (
    change_kind              text,
    source_vocabulary_id     varchar,
    source_code              varchar,
    source_code_description  varchar,
    n_added                  int,
    n_removed                int,
    n_modified               int,
    changed_fields           text[],
    old_targets              jsonb,
    new_targets              jsonb
) LANGUAGE sql STABLE AS $$
    WITH d AS (
        SELECT *
          FROM mapping.diff_releases(p_from, p_to)
    ),
    c AS (
        SELECT d.source_vocabulary_id,
               d.source_code,
               (count(*) FILTER (WHERE d.change_type = 'ADDED'))::int    AS n_added,
               (count(*) FILTER (WHERE d.change_type = 'REMOVED'))::int  AS n_removed,
               (count(*) FILTER (WHERE d.change_type = 'MODIFIED'))::int AS n_modified
          FROM d
         GROUP BY d.source_vocabulary_id, d.source_code
    ),
    f AS (
        SELECT d.source_vocabulary_id,
               d.source_code,
               array_agg(DISTINCT k ORDER BY k) AS fields
          FROM d
         CROSS JOIN LATERAL unnest(d.changed_fields) AS k
         GROUP BY d.source_vocabulary_id, d.source_code
    ),
    r AS (
        SELECT (SELECT release_id FROM mapping.release WHERE label = p_from) AS id_from,
               (SELECT release_id FROM mapping.release WHERE label = p_to)   AS id_to
    ),
    t AS (
        SELECT s.release_id,
               s.source_vocabulary_id,
               s.source_code,
               max(s.source_code_description) AS description,
               jsonb_agg(
                   jsonb_build_object(
                       'target_concept_id', s.target_concept_id,
                       'relationship_id',   s.relationship_id,
                       'mapping_status',    s.mapping_status,
                       'concept_name',      co.concept_name
                   )
                   ORDER BY s.relationship_id, s.target_concept_id
               ) AS targets
          FROM mapping.source_to_concept_map s
          JOIN c
            ON c.source_vocabulary_id = s.source_vocabulary_id
           AND c.source_code          = s.source_code
          LEFT JOIN vocab.concept co ON co.concept_id = s.target_concept_id
         WHERE s.release_id IN (SELECT id_from FROM r UNION ALL SELECT id_to FROM r)
         GROUP BY s.release_id, s.source_vocabulary_id, s.source_code
    )
    SELECT CASE
               WHEN o.targets IS NULL               THEN 'NEW_CODE'
               WHEN n.targets IS NULL               THEN 'REMOVED_CODE'
               WHEN c.n_added > 0 OR c.n_removed > 0 THEN 'TARGET_CHANGED'
               ELSE 'MODIFIED'
           END,
           c.source_vocabulary_id,
           c.source_code,
           coalesce(n.description, o.description),
           c.n_added,
           c.n_removed,
           c.n_modified,
           coalesce(f.fields, ARRAY[]::text[]),
           o.targets,
           n.targets
      FROM c
     CROSS JOIN r
      LEFT JOIN f
             ON f.source_vocabulary_id = c.source_vocabulary_id
            AND f.source_code          = c.source_code
      LEFT JOIN t o
             ON o.release_id           = r.id_from
            AND o.source_vocabulary_id = c.source_vocabulary_id
            AND o.source_code          = c.source_code
      LEFT JOIN t n
             ON n.release_id           = r.id_to
            AND n.source_vocabulary_id = c.source_vocabulary_id
            AND n.source_code          = c.source_code;
$$;
```

### `db/ddl/09_athena_reference.sql`

```sql
-- =====================================================================
-- 09 — Comparaison avec les mappings natifs d'Athena (relations « Maps to »)
-- Prérequis : 03_mapping.sql
--   * mapping.athena_connection     : bases Athena enregistrées (une seule active)
--   * mapping.athena_vocabulary_map : source_vocabulary_id local -> vocabulary_id Athena
--                                     (+ normalisation des codes : points, casse)
--   * mapping.athena_maps_to        : copie locale, à la demande, des concepts des
--                                     vocabulaires paramétrés et de leurs relations
--                                     « Maps to » / « Maps to value » valides
--   * mapping.normalize_code()      : clé de rapprochement d'un code
--   * mapping.compare_athena()      : statut de chaque mapping d'une release face à Athena
-- Le mot de passe est stocké en clair : utiliser un compte Athena en lecture seule.
-- =====================================================================

CREATE TABLE IF NOT EXISTS mapping.athena_connection (
    athena_connection_id  serial PRIMARY KEY,
    label                 text NOT NULL UNIQUE,
    host                  text NOT NULL,
    port                  int  NOT NULL DEFAULT 5432 CHECK (port BETWEEN 1 AND 65535),
    database_name         text NOT NULL,
    username              text NOT NULL,
    password              text,
    schema_name           text NOT NULL DEFAULT 'public' CHECK (schema_name ~ '^[A-Za-z_][A-Za-z0-9_]*$'),
    is_active             boolean NOT NULL DEFAULT false,
    athena_vocabulary_version text,
    last_sync_at          timestamptz,
    last_sync_status      text CHECK (last_sync_status IN ('ok', 'failed')),
    last_sync_message     text,
    last_sync_rows        int,
    created_at            timestamptz NOT NULL DEFAULT now()
);

-- Une seule connexion active à la fois
CREATE UNIQUE INDEX IF NOT EXISTS uq_athena_connection_one_active
    ON mapping.athena_connection (is_active) WHERE is_active;

CREATE TABLE IF NOT EXISTS mapping.athena_vocabulary_map (
    source_vocabulary_id  varchar(20) PRIMARY KEY,
    athena_vocabulary_id  varchar(20) NOT NULL,
    ignore_dots           boolean NOT NULL DEFAULT true,
    ignore_case           boolean NOT NULL DEFAULT true,
    created_at            timestamptz NOT NULL DEFAULT now()
);

-- Une ligne par (concept source Athena, cible) ; concept sans « Maps to » : cible NULL
CREATE TABLE IF NOT EXISTS mapping.athena_maps_to (
    vocabulary_id          varchar(20)  NOT NULL,
    concept_code           varchar(50)  NOT NULL,
    concept_id             int          NOT NULL,
    concept_name           varchar(255),
    invalid_reason         varchar(1),
    relationship_id        varchar(20),
    target_concept_id      int,
    target_concept_name    varchar(255),
    target_vocabulary_id   varchar(20),
    target_domain_id       varchar(20),
    target_standard_concept varchar(1)
);

CREATE INDEX IF NOT EXISTS idx_athena_maps_to_vocab_code ON mapping.athena_maps_to (vocabulary_id, concept_code);
CREATE INDEX IF NOT EXISTS idx_athena_maps_to_concept    ON mapping.athena_maps_to (concept_id);

CREATE OR REPLACE FUNCTION mapping.normalize_code(p_code text, p_ignore_dots boolean, p_ignore_case boolean)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN p_ignore_case THEN upper(v.code) ELSE v.code END
      FROM (SELECT CASE WHEN p_ignore_dots THEN replace(btrim(p_code), '.', '') ELSE btrim(p_code) END AS code) v;
$$;

-- Statuts :
--   SAME               : notre cible fait partie des cibles Athena (même relation)
--   DIFFERENT          : Athena propose d'autres cibles pour cette relation
--   MISSING_LOCAL      : non mappé chez nous (cible 0) alors qu'Athena propose une cible
--   ATHENA_NO_MAPPING  : code trouvé dans Athena mais sans « Maps to » pour cette relation
--   CODE_NOT_IN_ATHENA : code introuvable dans le vocabulaire Athena paramétré
-- Seuls les vocabulaires présents dans athena_vocabulary_map sont comparés.
CREATE OR REPLACE FUNCTION mapping.compare_athena(p_release_label text)
RETURNS TABLE (
    stcm_id                 bigint,
    source_vocabulary_id    varchar,
    source_code             varchar,
    source_code_description varchar,
    target_concept_id       int,
    target_concept_name     varchar,
    relationship_id         varchar,
    mapping_status          text,
    athena_vocabulary_id    varchar,
    athena_concept_id       int,
    athena_concept_code     varchar,
    athena_concept_name     varchar,
    athena_target_ids       int[],
    athena_targets          jsonb,
    comparison_status       text
) LANGUAGE sql STABLE AS $$
    WITH s AS (
        SELECT s.stcm_id,
               s.source_vocabulary_id,
               s.source_code,
               s.source_code_description,
               s.target_concept_id,
               s.relationship_id,
               s.mapping_status,
               m.athena_vocabulary_id,
               mapping.normalize_code(s.source_code, m.ignore_dots, m.ignore_case) AS code_key
          FROM mapping.source_to_concept_map s
          JOIN mapping.release r
            ON r.release_id = s.release_id
           AND r.label      = p_release_label
          JOIN mapping.athena_vocabulary_map m
            ON m.source_vocabulary_id = s.source_vocabulary_id
    ),
    a AS (
        SELECT m.source_vocabulary_id,
               mapping.normalize_code(a.concept_code, m.ignore_dots, m.ignore_case) AS code_key,
               a.*
          FROM mapping.athena_maps_to a
          JOIN mapping.athena_vocabulary_map m
            ON m.athena_vocabulary_id = a.vocabulary_id
    ),
    j AS (
        SELECT s.stcm_id,
               min(a.concept_id)                                                AS athena_concept_id,
               min(a.concept_code)                                              AS athena_concept_code,
               min(a.concept_name)                                              AS athena_concept_name,
               count(a.concept_id)                                              AS n_found,
               array_agg(DISTINCT a.target_concept_id ORDER BY a.target_concept_id)
                   FILTER (WHERE a.relationship_id = s.relationship_id)         AS target_ids,
               jsonb_agg(
                   DISTINCT jsonb_build_object(
                       'relationship_id',   a.relationship_id,
                       'target_concept_id', a.target_concept_id,
                       'concept_name',      a.target_concept_name,
                       'vocabulary_id',     a.target_vocabulary_id,
                       'domain_id',         a.target_domain_id
                   )
               ) FILTER (WHERE a.target_concept_id IS NOT NULL)                AS targets
          FROM s
          LEFT JOIN a
                 ON a.source_vocabulary_id = s.source_vocabulary_id
                AND a.code_key             = s.code_key
         GROUP BY s.stcm_id
    )
    SELECT s.stcm_id,
           s.source_vocabulary_id,
           s.source_code,
           s.source_code_description,
           s.target_concept_id,
           c.concept_name,
           s.relationship_id,
           s.mapping_status,
           s.athena_vocabulary_id,
           j.athena_concept_id,
           j.athena_concept_code,
           j.athena_concept_name,
           j.target_ids,
           j.targets,
           CASE
               WHEN j.n_found = 0                          THEN 'CODE_NOT_IN_ATHENA'
               WHEN j.target_ids IS NULL                   THEN 'ATHENA_NO_MAPPING'
               WHEN s.target_concept_id = ANY(j.target_ids) THEN 'SAME'
               WHEN s.target_concept_id = 0                THEN 'MISSING_LOCAL'
               ELSE 'DIFFERENT'
           END
      FROM s
      JOIN j ON j.stcm_id = s.stcm_id
      LEFT JOIN vocab.concept c ON c.concept_id = s.target_concept_id;
$$;
```

### `db/ddl/10_search_text.sql`

```sql
-- =====================================================================
-- 10 — Recherche plein texte insensible à la casse et aux accents
-- Prérequis : 03_mapping.sql
--   * extension unaccent (extension « trusted » : le propriétaire de la base peut la créer)
--   * mapping.normalize_text(text) : minuscules sans accents, IMMUTABLE (utilisable dans un index)
--   * index trigram sur le code et la description source normalisés
-- La recherche découpe la saisie en mots ; chaque mot doit apparaître dans le code, la
-- description source ou le libellé du concept cible (dans n'importe quel ordre).
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION mapping.normalize_text(p_value text)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT lower(public.unaccent('public.unaccent'::regdictionary, coalesce(p_value, '')));
$$;

CREATE INDEX IF NOT EXISTS idx_stcm_code_norm_trgm
    ON mapping.source_to_concept_map USING gin (mapping.normalize_text(source_code) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_stcm_desc_norm_trgm
    ON mapping.source_to_concept_map USING gin (mapping.normalize_text(source_code_description) gin_trgm_ops);
```

### `db/ddl/11_search_text_column.sql`

```sql
-- =====================================================================
-- 11 — Texte de recherche précalculé (rapidité de la recherche plein texte)
-- Prérequis : 05_diff_releases_perf.sql, 10_search_text.sql
--   * source_to_concept_map.search_text : code + description source, minuscules sans accents,
--     colonne générée (STORED) recalculée par PostgreSQL à chaque INSERT / UPDATE, index trigram
--   * les index d'expression de 10 deviennent inutiles et sont supprimés
--   * diff_releases (05) : search_text exclu de old_row / new_row / changed_fields (colonne technique)
-- =====================================================================

ALTER TABLE mapping.source_to_concept_map
    ADD COLUMN IF NOT EXISTS search_text text
    GENERATED ALWAYS AS (
        mapping.normalize_text(source_code || ' ' || coalesce(source_code_description, ''))
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_stcm_search_text_trgm
    ON mapping.source_to_concept_map USING gin (search_text gin_trgm_ops);

DROP INDEX IF EXISTS mapping.idx_stcm_code_norm_trgm;
DROP INDEX IF EXISTS mapping.idx_stcm_desc_norm_trgm;

CREATE OR REPLACE FUNCTION mapping.diff_releases(p_from text, p_to text)
RETURNS TABLE (
    change_type          text,
    source_vocabulary_id varchar,
    source_code          varchar,
    target_concept_id    int,
    relationship_id      varchar,
    changed_fields       text[],
    old_row              jsonb,
    new_row              jsonb
) LANGUAGE sql STABLE AS $$
    WITH excl AS (
        SELECT ARRAY['stcm_id', 'release_id', 'import_batch_id', 'created_at', 'updated_at', 'search_text'] AS cols
    ),
    a AS (
        SELECT s.*
          FROM mapping.source_to_concept_map s
         WHERE s.release_id = (SELECT r.release_id FROM mapping.release r WHERE r.label = p_from)
    ),
    b AS (
        SELECT s.*
          FROM mapping.source_to_concept_map s
         WHERE s.release_id = (SELECT r.release_id FROM mapping.release r WHERE r.label = p_to)
    ),
    d AS (
        SELECT CASE WHEN a.stcm_id IS NULL THEN NULL ELSE to_jsonb(a) - (SELECT cols FROM excl) END AS aj,
               CASE WHEN b.stcm_id IS NULL THEN NULL ELSE to_jsonb(b) - (SELECT cols FROM excl) END AS bj,
               coalesce(b.source_vocabulary_id, a.source_vocabulary_id) AS source_vocabulary_id,
               coalesce(b.source_code,          a.source_code)          AS source_code,
               coalesce(b.target_concept_id,    a.target_concept_id)    AS target_concept_id,
               coalesce(b.relationship_id,      a.relationship_id)      AS relationship_id
          FROM a
          FULL JOIN b
            ON  a.source_vocabulary_id = b.source_vocabulary_id
            AND a.source_code          = b.source_code
            AND a.target_concept_id    = b.target_concept_id
            AND a.relationship_id      = b.relationship_id
         WHERE a.stcm_id IS NULL
            OR b.stcm_id IS NULL
            OR (a.source_concept_id, a.source_code_description, a.target_vocabulary_id, a.valid_start_date,
                a.valid_end_date, a.invalid_reason, a.domain_id, a.source_frequency, a.mapping_status,
                a.equivalence, a.mapping_comment, a.mapped_by, a.reviewed_by, a.reviewed_at, a.extra)
               IS DISTINCT FROM
               (b.source_concept_id, b.source_code_description, b.target_vocabulary_id, b.valid_start_date,
                b.valid_end_date, b.invalid_reason, b.domain_id, b.source_frequency, b.mapping_status,
                b.equivalence, b.mapping_comment, b.mapped_by, b.reviewed_by, b.reviewed_at, b.extra)
    )
    SELECT
        CASE WHEN d.aj IS NULL THEN 'ADDED'
             WHEN d.bj IS NULL THEN 'REMOVED'
             ELSE 'MODIFIED' END,
        d.source_vocabulary_id,
        d.source_code,
        d.target_concept_id,
        d.relationship_id,
        CASE WHEN d.aj IS NOT NULL AND d.bj IS NOT NULL THEN
            ARRAY(SELECT k FROM jsonb_object_keys(d.bj) k
                   WHERE (d.aj -> k) IS DISTINCT FROM (d.bj -> k)
                   ORDER BY k)
        END,
        d.aj,
        d.bj
    FROM d;
$$;
```

### `db/ddl/12_status_lifecycle_and_import_rollback.sql`

```sql
-- =====================================================================
-- 12 — Statuts liés au cycle de release et annulation d'un import
-- Prérequis : 03, 06, 07, 11
--   * publish_release : à la publication, les mappings UNCHECKED deviennent APPROVED
--     (revus par l'utilisateur qui publie) ; IGNORED est conservé ; la publication est
--     refusée s'il reste des mappings FLAGGED.
--   * releases déjà publiées : leurs mappings UNCHECKED passent APPROVED (une seule fois,
--     verrou levé le temps de la mise à jour, modifications tracées dans audit_log).
--   * import_batch.loaded_at : horodatage de la transaction de chargement (= audit_log.changed_at
--     des lignes modifiées / supprimées par l'import).
--   * rollback_import(batch) : annule un import d'une release ouverte (supprime les lignes
--     ajoutées, restaure les lignes modifiées ou supprimées) ; le lot passe « rolled_back ».
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Publication : validation automatique des mappings non encore revus
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mapping.publish_release(p_label text, p_cdm_build_ref text DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    v_release_id int;
    v_status     text;
    v_flagged    int;
BEGIN
    SELECT release_id, status INTO v_release_id, v_status
      FROM mapping.release
     WHERE label = p_label;
    IF v_release_id IS NULL OR v_status NOT IN ('open', 'frozen') THEN
        RAISE EXCEPTION 'Release % introuvable ou déjà publiée/archivée', p_label;
    END IF;

    SELECT count(*) INTO v_flagged
      FROM mapping.source_to_concept_map
     WHERE release_id = v_release_id
       AND mapping_status = 'FLAGGED';
    IF v_flagged > 0 THEN
        RAISE EXCEPTION 'Release % : % mapping(s) FLAGGED à traiter avant publication', p_label, v_flagged;
    END IF;

    IF v_status = 'open' THEN
        UPDATE mapping.source_to_concept_map
           SET mapping_status = 'APPROVED',
               reviewed_at    = coalesce(reviewed_at, now()),
               reviewed_by    = coalesce(reviewed_by, nullif(current_setting('app.user', true), ''))
         WHERE release_id = v_release_id
           AND mapping_status = 'UNCHECKED';
    ELSIF EXISTS (
        SELECT 1 FROM mapping.source_to_concept_map
         WHERE release_id = v_release_id AND mapping_status = 'UNCHECKED'
    ) THEN
        RAISE EXCEPTION 'Release % figée avec des mappings UNCHECKED : la rouvrir pour les valider', p_label;
    END IF;

    UPDATE mapping.release
       SET status        = 'published',
           frozen_at     = coalesce(frozen_at, now()),
           published_at  = now(),
           cdm_build_ref = coalesce(p_cdm_build_ref, cdm_build_ref)
     WHERE release_id = v_release_id;
END $$;

-- ---------------------------------------------------------------------
-- 2. Releases déjà publiées : UNCHECKED -> APPROVED (rattrapage unique)
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM mapping.source_to_concept_map s
          JOIN mapping.release r ON r.release_id = s.release_id
         WHERE r.status = 'published'
           AND s.mapping_status = 'UNCHECKED'
    ) THEN
        PERFORM set_config('app.user', 'migration 12 (publication = validation)', true);
        ALTER TABLE mapping.source_to_concept_map DISABLE TRIGGER stcm_guard;
        UPDATE mapping.source_to_concept_map s
           SET mapping_status = 'APPROVED',
               reviewed_at    = coalesce(s.reviewed_at, r.published_at),
               updated_at     = now()
          FROM mapping.release r
         WHERE r.release_id = s.release_id
           AND r.status = 'published'
           AND s.mapping_status = 'UNCHECKED';
        ALTER TABLE mapping.source_to_concept_map ENABLE TRIGGER stcm_guard;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 3. Annulation d'un import
-- ---------------------------------------------------------------------
ALTER TABLE mapping.import_batch ADD COLUMN IF NOT EXISTS loaded_at timestamptz;

-- Lignes concernées par un import : ajoutées (INSERT), modifiées (UPDATE) ou supprimées (DELETE)
CREATE OR REPLACE FUNCTION mapping.import_effects(p_batch_id int)
RETURNS TABLE (stcm_id bigint, effect text, old_row jsonb)
LANGUAGE sql STABLE AS $$
    WITH b AS (
        SELECT release_id, loaded_at FROM mapping.import_batch WHERE import_batch_id = p_batch_id
    )
    SELECT s.stcm_id, 'INSERT', NULL::jsonb
      FROM mapping.source_to_concept_map s, b
     WHERE s.release_id = b.release_id
       AND s.import_batch_id = p_batch_id
       AND s.created_at = b.loaded_at
    UNION ALL
    SELECT a.stcm_id, a.operation, a.old_row
      FROM mapping.audit_log a, b
     WHERE a.release_id = b.release_id
       AND a.changed_at = b.loaded_at
       AND a.operation IN ('UPDATE', 'DELETE');
$$;

CREATE OR REPLACE FUNCTION mapping.rollback_import(p_batch_id int)
RETURNS TABLE (n_deleted int, n_restored int, n_reinserted int)
LANGUAGE plpgsql AS $$
DECLARE
    v_batch    mapping.import_batch%ROWTYPE;
    v_status   text;
    v_conflict int;
    v_deleted  int;
    v_restored int;
    v_reinserted int;
BEGIN
    SELECT * INTO v_batch FROM mapping.import_batch WHERE import_batch_id = p_batch_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Import % introuvable', p_batch_id;
    END IF;
    IF v_batch.status NOT IN ('loaded', 'partial') OR v_batch.loaded_at IS NULL THEN
        RAISE EXCEPTION 'Import % non annulable (statut %)', p_batch_id, v_batch.status;
    END IF;
    SELECT status INTO v_status FROM mapping.release WHERE release_id = v_batch.release_id;
    IF v_status <> 'open' THEN
        RAISE EXCEPTION 'Import % : la release n''est plus ouverte (statut %), import archivé', p_batch_id, v_status;
    END IF;

    CREATE TEMP TABLE tmp_effects ON COMMIT DROP AS
    SELECT * FROM mapping.import_effects(p_batch_id);

    -- Refus si une ligne concernée a été modifiée depuis (correction manuelle, autre import)
    SELECT count(*) INTO v_conflict
      FROM mapping.audit_log a
     WHERE a.release_id = v_batch.release_id
       AND a.changed_at > v_batch.loaded_at
       AND a.stcm_id IN (SELECT e.stcm_id FROM tmp_effects e);
    IF v_conflict > 0 THEN
        RAISE EXCEPTION 'Import % : % ligne(s) modifiée(s) depuis le chargement, annulation impossible',
            p_batch_id, v_conflict;
    END IF;

    -- 1. Lignes ajoutées par l'import
    DELETE FROM mapping.source_to_concept_map s
     USING tmp_effects e
     WHERE e.effect = 'INSERT'
       AND s.stcm_id = e.stcm_id;
    GET DIAGNOSTICS v_deleted = ROW_COUNT;

    -- 2. Lignes supprimées par l'import (mode replace_vocabulary) : réinsérées à l'identique
    INSERT INTO mapping.source_to_concept_map (
        stcm_id, release_id, source_code, source_concept_id, source_vocabulary_id, source_code_description,
        target_concept_id, target_vocabulary_id, valid_start_date, valid_end_date, invalid_reason,
        domain_id, relationship_id, source_frequency, mapping_status, equivalence, mapping_comment,
        mapped_by, reviewed_by, reviewed_at, import_batch_id, extra, created_at, updated_at
    )
    SELECT r.stcm_id, r.release_id, r.source_code, r.source_concept_id, r.source_vocabulary_id,
           r.source_code_description, r.target_concept_id, r.target_vocabulary_id, r.valid_start_date,
           r.valid_end_date, r.invalid_reason, r.domain_id, r.relationship_id, r.source_frequency,
           r.mapping_status, r.equivalence, r.mapping_comment, r.mapped_by, r.reviewed_by, r.reviewed_at,
           r.import_batch_id, r.extra, r.created_at, r.updated_at
      FROM tmp_effects e
     CROSS JOIN LATERAL jsonb_populate_record(NULL::mapping.source_to_concept_map, e.old_row) r
     WHERE e.effect = 'DELETE';
    GET DIAGNOSTICS v_reinserted = ROW_COUNT;

    -- 3. Lignes modifiées par l'import : valeurs d'avant le chargement
    UPDATE mapping.source_to_concept_map s
       SET source_concept_id       = r.source_concept_id,
           source_code_description = r.source_code_description,
           target_vocabulary_id    = r.target_vocabulary_id,
           valid_start_date        = r.valid_start_date,
           valid_end_date          = r.valid_end_date,
           invalid_reason          = r.invalid_reason,
           domain_id               = r.domain_id,
           source_frequency        = r.source_frequency,
           mapping_status          = r.mapping_status,
           equivalence             = r.equivalence,
           mapping_comment         = r.mapping_comment,
           mapped_by               = r.mapped_by,
           reviewed_by             = r.reviewed_by,
           reviewed_at             = r.reviewed_at,
           import_batch_id         = r.import_batch_id,
           extra                   = r.extra
      FROM tmp_effects e
     CROSS JOIN LATERAL jsonb_populate_record(NULL::mapping.source_to_concept_map, e.old_row) r
     WHERE e.effect = 'UPDATE'
       AND s.stcm_id = e.stcm_id;
    GET DIAGNOSTICS v_restored = ROW_COUNT;

    UPDATE mapping.import_batch SET status = 'rolled_back' WHERE import_batch_id = p_batch_id;
    DROP TABLE tmp_effects;

    RETURN QUERY SELECT v_deleted, v_restored, v_reinserted;
END $$;
```
