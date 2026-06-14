# KFC Senegal - Commande Automatisee

Script Python qui automatise les commandes sur [kfcsenegal.sn](https://kfcsenegal.sn).

## Fonctionnalites

- Enregistrement des informations utilisateur (nom, telephone, email, adresse) en base SQLite
- Zone de livraison par defaut : **SICAP LIBERTE 1/4** (selection automatique)
- Historique des commandes avec rechargement automatique
- 121 zones de livraison disponibles (recuperees depuis le site KFC)
- Affichage du menu KFC Bourguiba avec categories et prix
- Ajout d'articles au panier avec quantite
- Changement de zone de livraison a tout moment
- Ajout de nouveaux profils utilisateurs
- Finalisation de la commande avec paiement en especes
- Envoi d'un email de confirmation apres la commande
- Navigateur Chromium en mode headless (invisible) par defaut

## Commandes disponibles

### Ligne de commande

| Commande | Description |
|----------|-------------|
| `uv run python kfc_order.py` | Lancer en mode headless (invisible) |
| `uv run python kfc_order.py --visible` | Lancer en mode visible (navigateur affiche) |
| `uv run python kfc_order.py --help` | Afficher l'aide |

### Au demarrage (profil)

| Entree | Description |
|--------|-------------|
| `Y` | Modifier les informations du profil |
| `N` | Garder le profil actuel |
| Autre touche | Creer un nouveau profil |

### Apres le chargement du menu

| Entree | Description |
|--------|-------------|
| `Y` | Refaire la derniere commande |
| `N` | Commander manuellement |

### Pendant la selection d'articles

| Entree | Description |
|--------|-------------|
| `1` a `999` | Numero de l'article a ajouter au panier |
| `lieu` | Changer la zone de livraison (affiche les 121 zones KFC) |
| `nouveau` | Ajouter un nouveau profil utilisateur |

### Quantite

| Entree | Description |
|--------|-------------|
| `1`, `2`, `3`... | Quantite de l'article |
| Entree vide | Quantite precedente ou defaut = 1 |

### Apres chaque ajout

| Entree | Description |
|--------|-------------|
| `Y` | Ajouter un autre article |
| `N` | Terminer la selection, passer au paiement |

### Recapitulatif

| Entree | Description |
|--------|-------------|
| `Y` | Confirmer et valider la commande |
| `N` | Annuler la commande |

## Prerequis

- Python >= 3.10
- [UV](https://docs.astral.sh/uv/) (gestionnaire de paquets Python)
- Playwright (installe automatiquement via UV)

## Installation

```bash
# Cloner le depot
git clone git@github.com:Dar-rius/fuckKFC.git
cd fuckKFC

# Installer les dependances avec UV
uv sync

# Installer le navigateur Chromium pour Playwright
uv run playwright install chromium
```

## Deroulement du script

1. **Premier lancement** : le script demande nom, telephone, email et adresse. Ces donnees sont sauvegardees dans `kfc_users.db`.

2. **Zones suivantes** : les informations sont affichees. Options : `Y` modifier, `N` garder, ou autre touche pour creer un nouveau profil.

3. **Zone automatique** : la zone enregistree (SICAP LIBERTE par defaut) est selectionnee automatiquement sur le site.

4. **Menu + rechargement** : le menu s'affiche. Si tu as deja commande, le script propose de recharger ta derniere commande avec les memes articles. Tu peux modifier les quantites.

5. **Commande manuelle** : sinon, entre les numeros des articles. Commandes utiles : `lieu` pour changer de zone, `nouveau` pour ajouter un profil.

6. **Finalisation** : le script remplit les informations, selectionne le paiement en especes et valide.

7. **Email + historique** : un email de confirmation est envoye et la commande est enregistreee dans l'historique.

## Structure du projet

```
fuckKFC/
├── pyproject.toml     # Configuration UV et dependances
├── README.md          # Cette documentation
├── kfc_order.py       # Script principal
├── database.py        # Module de gestion SQLite3
└── kfc_users.db       # Base de donnees (generee automatiquement)
```

## Base de donnees

Le fichier `kfc_users.db` est cree automatiquement. Tables :

### users

| Champ      | Type   | Description                     |
|------------|--------|---------------------------------|
| id         | INTEGER| Identifiant auto-incremente     |
| nom        | TEXT   | Nom complet                     |
| telephone  | TEXT   | Numero de telephone             |
| email      | TEXT   | Adresse email                   |
| adresse    | TEXT   | Adresse de livraison            |
| zone_id    | TEXT   | ID de la zone de livraison      |
| zone_name  | TEXT   | Nom de la zone de livraison     |

### orders

| Champ      | Type   | Description                     |
|------------|--------|---------------------------------|
| id         | INTEGER| Identifiant auto-incremente     |
| user_id    | INTEGER| ID de l'utilisateur             |
| items      | TEXT   | Articles en JSON                |
| zone_id    | TEXT   | ID de la zone                   |
| zone_name  | TEXT   | Nom de la zone                  |
| created_at | TEXT   | Date et heure de la commande    |

## Notes techniques

- Le site kfcsenegal.sn est une SPA Laravel Inertia.js + Vue 3
- Le script utilise Playwright pour interagir avec le navigateur
- Le paiement est configure en especes par defaut
- Les zones de livraison sont extraites depuis la page d'accueil du site
- Le menu est charge dynamiquement par le site
- Le site a un systeme de rate-limiting : si vous voyez "HTTP 429", le script attend automatiquement
- L'email de confirmation est envoye via SMTP local (port 25)

## Auteurs

- [Dar-rius](https://github.com/Dar-rius)
