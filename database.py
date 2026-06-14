"""
Module de gestion de la base de donnees SQLite3.
Stocke les informations utilisateur (nom, telephone, email, adresse, zone).
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "kfc_users.db"

DEFAULT_ZONE = {"id": "90", "name": "SICAP LIBERTE 1/4", "frais": 1000}


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Cree la table users si elle n'existe pas, ou met a jour le schema."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT NOT NULL,
                telephone TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '',
                adresse TEXT NOT NULL,
                zone_id TEXT NOT NULL DEFAULT '90',
                zone_name TEXT NOT NULL DEFAULT 'SICAP LIBERTE 1/4'
            )
            """
        )

        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "email" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
        if "zone_id" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN zone_id TEXT NOT NULL DEFAULT '90'")
        if "zone_name" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN zone_name TEXT NOT NULL DEFAULT 'SICAP LIBERTE 1/4'")

        conn.commit()


def save_user(nom: str, telephone: str, email: str, adresse: str,
              zone_id: str = "90", zone_name: str = "SICAP LIBERTE 1/4") -> None:
    """Enregistre un nouvel utilisateur."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO users (nom, telephone, email, adresse, zone_id, zone_name) VALUES (?, ?, ?, ?, ?, ?)",
            (nom, telephone, email, adresse, zone_id, zone_name),
        )
        conn.commit()


def update_user(**kwargs) -> None:
    """Met a jour les champs passes en argument du dernier utilisateur."""
    with get_connection() as conn:
        user = conn.execute("SELECT id FROM users ORDER BY id DESC LIMIT 1").fetchone()
        if not user:
            return
        uid = user["id"]
        for key, val in kwargs.items():
            if key in ("nom", "telephone", "email", "adresse", "zone_id", "zone_name"):
                conn.execute(f"UPDATE users SET {key} = ? WHERE id = ?", (val, uid))
        conn.commit()


def get_user() -> dict | None:
    """Recupere le dernier utilisateur enregistre."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT nom, telephone, email, adresse, zone_id, zone_name FROM users ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            return {
                "nom": row["nom"],
                "telephone": row["telephone"],
                "email": row["email"],
                "adresse": row["adresse"],
                "zone_id": row["zone_id"],
                "zone_name": row["zone_name"],
            }
        return None


def user_exists() -> bool:
    """Verifie si un utilisateur est deja enregistre."""
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return count > 0
