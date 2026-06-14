# KFC Senegal - Automated Ordering

Python script that automates orders on [kfcsenegal.sn](https://kfcsenegal.sn).

## Features

- User info storage (name, phone, email, address) in SQLite
- Default delivery zone: **SICAP LIBERTE 1/4** (auto-selected)
- Order history with auto-reload of last order
- Delivery zones fetched from the KFC website
- KFC Bourguiba menu display with categories and prices
- Add items to cart with quantity
- Change delivery zone at any time
- Add new user profiles
- Order checkout with cash payment
- Chromium browser in headless mode (invisible) by default

## Commands

### CLI

| Command | Description |
|---------|-------------|
| `uv run python kfc_order.py` | Launch in headless mode (invisible) |
| `uv run python kfc_order.py --visible` | Launch in visible mode (browser shown) |
| `uv run python kfc_order.py --help` | Show help |

### At startup (profile)

| Input | Description |
|-------|-------------|
| `Y` | Edit profile info |
| `N` | Keep current profile |
| Any other key | Create a new profile |

### After menu loads

| Input | Description |
|-------|-------------|
| `Y` | Reorder the last order |
| `N` | Order manually |

### During item selection

| Input | Description |
|-------|-------------|
| `1` to `999` | Item number to add to cart |
| `lieu` | Change delivery zone |
| `nouveau` | Add a new user profile |

### Quantity

| Input | Description |
|-------|-------------|
| `1`, `2`, `3`... | Item quantity |
| Empty | Default = 1 |

### After each addition

| Input | Description |
|-------|-------------|
| `Y` | Add another item |
| `N` | Finish selection, proceed to checkout |

### Summary

| Input | Description |
|-------|-------------|
| `Y` | Confirm and place order |
| `N` | Cancel order |

## Prerequisites

- Python >= 3.10
- [UV](https://docs.astral.sh/uv/) (Python package manager)
- Playwright (installed automatically via UV)

## Installation

```bash
# Clone the repo
git clone git@github.com:Dar-rius/fuckKFC.git
cd fuckKFC

# Install dependencies with UV
uv sync

# Install Chromium for Playwright
uv run playwright install chromium
```

## How it works

1. **First run**: the script asks for name, phone, email and address. Data is saved to `kfc_users.db`.

2. **Subsequent runs**: info is displayed. Options: `Y` to edit, `N` to keep, or any other key for a new profile.

3. **Auto zone**: the saved zone (SICAP LIBERTE by default) is auto-selected on the site.

4. **Menu + reorder**: the menu is displayed. If you've ordered before, the script offers to reload your last order with the same items. You can modify quantities.

5. **Manual ordering**: otherwise, enter item numbers. Useful commands: `lieu` to change zone, `nouveau` to add a profile.

6. **Checkout**: the script fills in your info, selects cash payment, and confirms.

7. **History**: the order is saved to history for future reordering.

## Project structure

```
fuckKFC/
├── pyproject.toml     # UV config and dependencies
├── README.md          # This documentation
├── kfc_order.py       # Main script
├── database.py        # SQLite3 database module
└── kfc_users.db       # Database (auto-generated)
```

## Database

`kfc_users.db` is created automatically. Tables:

### users

| Field     | Type    | Description                |
|-----------|---------|----------------------------|
| id        | INTEGER | Auto-incremented ID        |
| nom       | TEXT    | Full name                  |
| telephone | TEXT    | Phone number               |
| email     | TEXT    | Email address              |
| adresse   | TEXT    | Delivery address           |
| zone_id   | TEXT    | Delivery zone ID           |
| zone_name | TEXT    | Delivery zone name         |

### orders

| Field     | Type    | Description                |
|-----------|---------|----------------------------|
| id        | INTEGER | Auto-incremented ID        |
| user_id   | INTEGER | User ID                    |
| items     | TEXT    | Items as JSON              |
| zone_id   | TEXT    | Zone ID                    |
| zone_name | TEXT    | Zone name                  |
| created_at| TEXT    | Order date and time        |

## Technical notes

- kfcsenegal.sn is a Laravel Inertia.js + Vue 3 SPA
- Playwright is used to interact with the browser
- Payment defaults to cash on delivery
- Delivery zones are extracted from the site's landing page
- Menu is loaded dynamically by the site
- The site has rate-limiting: if you see "HTTP 429", the script waits automatically

## Authors

- [Dar-rius](https://github.com/Dar-rius)
