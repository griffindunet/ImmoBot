# Appart.py

Application Python/Flask de veille immobilière orientée appartements, avec stockage SQLite, filtrage local et interface web simple pour consulter les annonces récupérées depuis Leboncoin. [file:1]

## Avertissement important

Ce script doit être exécuté depuis une **connexion résidentielle / IP domicile** et **pas depuis un serveur, VPS, cloud, datacenter ou machine hébergée**. [file:1]

Le fonctionnement repose sur des requêtes web vers des services externes et un usage depuis une IP de serveur peut entraîner des blocages, limitations, captchas ou un fonctionnement instable. [file:1]

## Fonctionnalités

- Recherche d'annonces d'appartements selon une liste de villes. [file:1]
- Filtres de prix minimum/maximum, surface minimum et nombre minimum de pièces. [file:1]
- Exclusion de mots-clés comme `viager`, `cave uniquement` ou `vendu loué`. [file:1]
- Vérification de la présence d'un parking, garage, box ou stationnement dans le texte ou les attributs de l'annonce. [file:1]
- Géocodage des villes via Nominatim OpenStreetMap. [file:1]
- Sauvegarde des annonces dans une base SQLite locale. [file:1]
- Interface Flask avec consultation des annonces enregistrées. [file:1]
- Boucle planifiée de scraping selon un intervalle en minutes. [file:1]

## Fichiers utilisés

- `appart.py` : script principal. [file:1]
- `config.json` : configuration de recherche créée automatiquement si absente. [file:1]
- `annonces.db` : base SQLite locale contenant les annonces collectées. [file:1]

## Pré-requis

- Python 3.10 ou plus récent recommandé.
- Dépendances Python du script, notamment `flask`, `requests` et la librairie `lbc`. [file:1]

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install flask requests lbc
```

Selon la version de la librairie `lbc`, le nom exact du package peut varier. Vérifier l'environnement avant exécution.

## Configuration

Au premier lancement, le script génère un fichier `config.json` avec une configuration par défaut contenant notamment : [file:1]

- `villes` : villes recherchées, par défaut `Cannes` et `Antibes`. [file:1]
- `prix_min` / `prix_max`. [file:1]
- `surface_min`. [file:1]
- `nb_pieces_min`. [file:1]
- `mots_exclus`. [file:1]
- `intervalle_minutes`. [file:1]
- `db_path`. [file:1]

Exemple de configuration :

```json
{
  "search": {
    "villes": ["Cannes", "Antibes"],
    "prix_min": 100000,
    "prix_max": 190000,
    "surface_min": 30,
    "nb_pieces_min": 2,
    "mots_exclus": ["viager", "cave uniquement", "vendu loué", "vendu loue"]
  },
  "intervalle_minutes": 30,
  "db_path": "annonces.db"
}
```

## Exécution

### Lancement local recommandé

```bash
python3 appart.py
```

Exécuter ce script sur un PC à la maison, une box internet, ou une machine connectée via une IP résidentielle. **Ne pas le lancer sur un VPS, un dédié, un serveur Docker public, un cloud provider ou une machine en datacenter.**

## Comportement du script

Le script : [file:1]

1. charge la configuration depuis `config.json` ou crée une configuration par défaut ; [file:1]
2. initialise une base SQLite si nécessaire ; [file:1]
3. géocode chaque ville configurée via Nominatim ; [file:1]
4. interroge Leboncoin avec les filtres immobiliers ; [file:1]
5. filtre les annonces localement ; [file:1]
6. enregistre les nouvelles annonces en base ; [file:1]
7. relance la recherche selon l'intervalle configuré. [file:1]

## Notes

- Le script semble conçu pour un usage local et persistant avec stockage sur disque. [file:1]
- Les résultats dépendent des réponses des services externes et de la compatibilité de la librairie `lbc`. [file:1]
- En cas de blocage réseau ou de résultats incohérents, tester en priorité depuis une vraie IP domicile. [file:1]
