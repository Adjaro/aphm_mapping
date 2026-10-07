# Référentiel mappings OMOP — AP-HM

Plateforme web interne qui versionne et expose la table `source_to_concept_map` (mappings des codes
locaux AP-HM vers les concepts standards OMOP), avec une ergonomie inspirée d'Athena.
La référence du projet (architecture, règles, conventions) est [CLAUDE.md](CLAUDE.md).

## Démarrage rapide (poste de développement)

Prérequis : Python 3.11+, PostgreSQL 16 (`docker compose up -d` fournit une instance de développement).

```bash
python -m venv .venv && source .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # adapter DATABASE_URL / TEST_DATABASE_URL

python scripts/migrate.py                               # applique db/ddl/*.sql
python scripts/load_vocab.py --dir /chemin/athena       # vocabulaire Athena (CONCEPT.csv, VOCABULARY.csv)
python scripts/load_usagi_dir.py --dir data --publish build-initial --staging v1.1

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Puis ouvrir http://localhost:8000.

### Chargement des exports Usagi (`data/`)

`scripts/load_usagi_dir.py` importe chaque CSV du répertoire par le **circuit d'import standard**
(contrôles SQL, `import_batch`, rejets) en mode `upsert` dans la release ouverte :

| Colonne Usagi | Colonne `source_to_concept_map` |
|---|---|
| `sourceCode` / `sourceName` | `source_code` / `source_code_description` |
| `sourceVocabulary` | `source_vocabulary_id` (*) |
| `conceptId` / `vocabulary_id` / `domain_id` | `target_concept_id` / `target_vocabulary_id` / `domain_id` |
| `mappingStatus` ou `mapping_status`, `equivalence`, `mappingType` | `mapping_status`, `equivalence`, `relationship_id` |
| `comment`, `createdBy`/`created_by`, `statusSetBy` | `mapping_comment`, `mapped_by`, `reviewed_by` |
| `sourceFrequency` ou `nbr` | `source_frequency` |

(*) deux identifiants dépassent `varchar(20)` et sont raccourcis : `aphm_biologie_interpretation` →
`aphm_bio_interp`, `aphm_voies_administration` → `aphm_voies_admin`.

**Vocabulaire de démonstration** : si `vocab.concept` est vide, le script l'alimente avec les concepts
cibles décrits dans les fichiers (≈ 23 000 concepts). C'est un extrait : charger le vocabulaire Athena
complet avec `load_vocab.py` avant d'utiliser le sélecteur de concepts en production.

## Qualité

```bash
ruff check . && ruff format --check . && mypy app scripts tests
pytest -q          # utilise TEST_DATABASE_URL, schéma recréé depuis db/ddl/ à chaque session
```

## Performances mesurées (300 000 lignes, PostgreSQL 16 non réglé, poste Windows)

| Mode | Durée |
|---|---|
| `insert` | ≈ 33 s |
| `upsert` sans changement (lignes identiques non réécrites) | ≈ 17 s |
| `upsert` avec 300 000 modifications réelles | ≈ 59 s |
| `replace_vocabulary` (300 000 suppressions + 300 000 insertions) | ≈ 46 s |

Le coût restant d'un upsert massif vient de l'audit (une ligne `audit_log` par mapping modifié) et de la
maintenance des index trigram ; un serveur PostgreSQL réglé (`shared_buffers`, `work_mem`) fera mieux.
