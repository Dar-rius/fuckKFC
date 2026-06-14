#!/usr/bin/env python3
"""
KFC Senegal - Script de commande automatise.

Ce script automatise le processus de commande sur kfcsenegal.sn :
1. Enregistre les informations utilisateur dans SQLite (une seule fois)
2. Ouvre le site via Playwright (navigateur headless)
3. Selectionne la zone de livraison (SICAP LIBERTE par defaut)
4. Affiche le menu et permet d'ajouter des articles au panier
5. Finalise la commande avec paiement en especes
6. Envoie un email de confirmation

Commandes utiles pendant la commande :
    lieu    - Changer la zone de livraison
    nouveau - Ajouter un nouveau profil utilisateur

Usage:
    uv run python kfc_order.py [--visible]
"""

import json
import smtplib
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from database import (
    init_db, get_user, save_user, update_user, user_exists,
    DEFAULT_ZONE,
)

BASE_URL = "https://kfcsenegal.sn"
RESTAURANT_URL = f"{BASE_URL}/restaurant/kfc-bourguiba"
RESTAURANT_DETAIL_URL = f"{BASE_URL}/restaurant-detail/kfc-bourguiba"
COMMANDE_URL = f"{BASE_URL}/commande"

ZONES_CACHE: list[dict] = []


def navigate_with_retry(page, url: str, max_retries: int = 3, wait_between: int = 30) -> bool:
    """Navigate to URL with retry on rate limiting (HTTP 429)."""
    for attempt in range(max_retries):
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(3)

            content = page.evaluate("() => document.body.innerText || ''")
            title = page.evaluate("() => document.title || ''")
            if "429" in title or "Slow down" in content or "rate limit" in content.lower():
                if attempt < max_retries - 1:
                    print(f"  Rate-limite (429). Attente de {wait_between}s... (tentative {attempt + 1}/{max_retries})")
                    time.sleep(wait_between)
                    continue
                else:
                    print("  Trop de requetes. Reessayez plus tard.")
                    return False
            return True
        except PwTimeout:
            if attempt < max_retries - 1:
                time.sleep(wait_between)
                continue
            return False
    return False


def setup_user() -> dict:
    """Gere l'enregistrement ou la recuperation des infos utilisateur.
    Premier lancement : demande nom, telephone, email, adresse.
    Suivants : affiche les infos et demande si modification.
    Tape 'nouveau' pour ajouter un autre profil.
    """
    init_db()

    if user_exists():
        user = get_user()
        print("\n  --- Vos informations ---")
        print(f"  Nom       : {user['nom']}")
        print(f"  Telephone : {user['telephone']}")
        print(f"  Email     : {user['email']}")
        print(f"  Adresse   : {user['adresse']}")
        print(f"  Zone      : {user['zone_name']}")
        print("\n  (Y) Modifier  (N) Garder  (autre) Nouveau profil")
        choix = input("  > ").strip().upper()
        if choix == "Y":
            user = _ask_user_info()
            update_user(
                nom=user["nom"], telephone=user["telephone"],
                email=user["email"], adresse=user["adresse"],
            )
            print("  Informations mises a jour !")
            return user
        elif choix == "N":
            return user
        else:
            print("\n  --- Nouveau profil ---")
            user = _ask_user_info()
            save_user(user["nom"], user["telephone"], user["email"], user["adresse"])
            print("  Nouveau profil enregistre !")
            return user

    print("\n  === Premier lancement ===")
    print("  Veuillez entrer vos informations pour la commande.\n")
    user = _ask_user_info()
    save_user(user["nom"], user["telephone"], user["email"], user["adresse"])
    print("\n  Informations enregistrees !")
    return user


def _ask_user_info() -> dict:
    """Demande les informations utilisateur (nom, telephone, email, adresse)."""
    nom = input("  Nom complet : ").strip()
    telephone = input("  Telephone   : ").strip()
    email = input("  Email       : ").strip()
    adresse = input("  Adresse     : ").strip()
    return {
        "nom": nom,
        "telephone": telephone,
        "email": email,
        "adresse": adresse,
        "zone_id": DEFAULT_ZONE["id"],
        "zone_name": DEFAULT_ZONE["name"],
    }


def get_zones(page) -> list[dict]:
    """Extrait les zones de livraison depuis la page."""
    global ZONES_CACHE
    if ZONES_CACHE:
        return ZONES_CACHE

    zones = page.evaluate("""
        () => {
            const options = document.querySelectorAll('#zone option[value]');
            return Array.from(options).map(opt => ({
                id: opt.value,
                name: opt.textContent.trim(),
                frais: parseInt(opt.getAttribute('data-frais') || '0')
            })).filter(z => z.id !== '');
        }
    """)
    ZONES_CACHE = zones
    return zones


def ask_zone(page, user: dict) -> dict:
    """Demande a l'utilisateur de choisir une zone de livraison.
    Si 'lieu' est tape, propose de changer la zone.
    Retourne la zone selectionnee.
    """
    current_zone = {"id": user.get("zone_id", DEFAULT_ZONE["id"]),
                    "name": user.get("zone_name", DEFAULT_ZONE["name"])}

    zones = get_zones(page)
    if not zones:
        print("  Aucune zone trouvee. Zone par defaut utilisee.")
        return current_zone

    print(f"\n  Zone actuelle : {current_zone['name']}")
    print("\n  Zones de livraison KFC disponibles :")
    for i, z in enumerate(zones, 1):
        frais = f"{z['frais']:,} FCFA".replace(",", " ")
        marker = " <-- actuelle" if z["id"] == current_zone["id"] else ""
        print(f"  [{i}] {z['name']} - Frais: {frais}{marker}")

    choix = input("\n  Selectionnez votre zone (numero) : ").strip()
    try:
        zone_idx = int(choix) - 1
        selected = zones[zone_idx]
    except (ValueError, IndexError):
        print("  Selection invalide, zone actuelle gardee.")
        return current_zone

    update_user(zone_id=selected["id"], zone_name=selected["name"])
    return selected


def select_zone_on_site(page, zone_id: str) -> None:
    """Selectionne une zone via Choices.js et soumet le formulaire."""
    page.evaluate(f"""
        () => {{
            const sel = document.querySelector('select[name="zone"]');
            if (sel) {{
                sel.value = '{zone_id}';
                sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
            const choicesItems = document.querySelectorAll('.choices__item[data-value="{zone_id}"]');
            if (choicesItems.length > 0) {{
                choicesItems[0].click();
            }}
        }}
    """)
    time.sleep(1)

    form = page.query_selector("form")
    if form:
        form.evaluate("f => f.submit()")
    else:
        page.click("button.btn-order, button[type='submit']")

    page.wait_for_load_state("networkidle", timeout=15000)
    time.sleep(2)


def scrape_menu(page) -> list[dict]:
    """Recupere les produits du menu via les props Inertia."""
    return page.evaluate("""
        () => {
            const appEl = document.querySelector('#app[data-page]');
            if (!appEl) return [];
            try {
                const pageData = JSON.parse(appEl.getAttribute('data-page'));
                const props = pageData.props || {};
                const categories = props.categories || props.restaurant?.categories || [];
                const products = [];
                if (Array.isArray(categories)) {
                    for (const cat of categories) {
                        const items = cat.produits || cat.products || [];
                        for (const item of items) {
                            products.push({
                                id: item.id,
                                nom: item.nom || item.name || item.libelle || '',
                                description: item.description || '',
                                prix: item.prix || item.price || 0,
                                image: item.image || item.photo || '',
                                category: cat.nom || cat.name || cat.libelle || '',
                                disponible: item.disponible !== false
                            });
                        }
                    }
                }
                return products;
            } catch(e) { return []; }
        }
    """)


def scrape_menu_from_dom(page) -> list[dict]:
    """Fallback : recupere les produits depuis le DOM rendu."""
    return page.evaluate("""
        () => {
            const products = [];
            const selectors = [
                '.product-card', '[data-product-id]', '.card-product',
                '.food-card', '.menu-item', '.product-item',
                '[class*="product"]', '[class*="menu"]'
            ];
            const seen = new Set();

            for (const sel of selectors) {
                const cards = document.querySelectorAll(sel);
                cards.forEach(card => {
                    const id = card.getAttribute('data-product-id')
                        || card.getAttribute('data-id')
                        || card.getAttribute('data-produit-id') || '';
                    if (id && seen.has(id)) return;
                    if (id) seen.add(id);

                    const nameEl = card.querySelector(
                        '.product-title, .card-title, h3, h4, .product-name, .item-name, [class*="title"], [class*="name"]'
                    );
                    const priceEl = card.querySelector(
                        '.product-price, .card-price, .price, span[style*="color"], [class*="price"]'
                    );
                    const imgEl = card.querySelector('img');
                    const addBtn = card.querySelector(
                        'button[data-product-id], button[onclick], .add-to-cart, [class*="add"], [class*="cart"]'
                    );
                    const btnId = addBtn
                        ? (addBtn.getAttribute('data-product-id')
                            || addBtn.getAttribute('data-produit-id')
                            || addBtn.getAttribute('onclick')?.match(/\\d+/)?.[0] || '')
                        : '';

                    if (nameEl && nameEl.textContent.trim().length > 0) {
                        products.push({
                            id: id || btnId || String(products.length + 1),
                            nom: nameEl.textContent.trim(),
                            prix: priceEl ? priceEl.textContent.trim() : '0',
                            image: imgEl ? imgEl.src : '',
                            disponible: !card.classList.contains('disabled')
                                && !card.querySelector('.out-of-stock, .unavailable')
                        });
                    }
                });
            }

            if (products.length === 0) {
                const allBtns = document.querySelectorAll('button');
                allBtns.forEach(btn => {
                    const onclick = btn.getAttribute('onclick') || '';
                    const match = onclick.match(/addToCart\\((\\d+)/i) || onclick.match(/ajouter\\((\\d+)/i);
                    if (match) {
                        const card = btn.closest('.card, .product, [class*="item"], div');
                        const name = card ? (card.querySelector('h3, h4, .title, .name') || {}).textContent : null;
                        if (name) {
                            products.push({
                                id: match[1],
                                nom: name.trim(),
                                prix: '0',
                                image: '',
                                disponible: true
                            });
                        }
                    }
                });
            }

            return products;
        }
    """)


def add_to_cart(page, product_id: str, quantite: int = 1) -> bool:
    """Ajoute un produit au panier via le bouton ajouter."""
    try:
        btn = page.query_selector(
            f'button[data-product-id="{product_id}"], '
            f'button[onclick*="{product_id}"], '
            f'[data-product-id="{product_id}"] button'
        )
        if btn:
            btn.click()
            time.sleep(1)

        if quantite > 1:
            plus_btn = page.query_selector(
                '.quantity-plus, button:has-text("+"), [data-action="increase"]'
            )
            for _ in range(quantite - 1):
                if plus_btn:
                    plus_btn.click()
                    time.sleep(0.3)

        return True
    except Exception as e:
        print(f"  Erreur ajout panier: {e}")
        return False


def add_to_cart_api(page, product_id: str, quantite: int = 1) -> bool:
    """Ajoute un produit au panier via l'API."""
    try:
        result = page.evaluate("""
            async ({productId, quantite}) => {
                try {
                    const response = await fetch('/api/panier/ajouter', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Requested-With': 'XMLHttpRequest',
                            'Accept': 'application/json',
                        },
                        body: JSON.stringify({
                            produit_id: parseInt(productId),
                            quantite: quantite
                        }),
                        credentials: 'same-origin'
                    });
                    const data = await response.json();
                    return { ok: response.ok, status: response.status, data: data };
                } catch(e) {
                    return { ok: false, error: e.message };
                }
            }
        """, {"productId": product_id, "quantite": quantite})
        return result.get("ok", False)
    except Exception as e:
        print(f"  Erreur API panier: {e}")
        return False


def checkout(page, user: dict) -> bool:
    """Remplit le formulaire de commande et valide."""
    print("\n  Navigation vers la page de commande...")
    if not navigate_with_retry(page, COMMANDE_URL):
        print("  Impossible de charger la page de commande.")
        return False
    time.sleep(3)

    page.evaluate("""
        (userData) => {
            const setVal = (selector, value) => {
                const el = document.querySelector(selector);
                if (el) {
                    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    )?.set || Object.getOwnPropertyDescriptor(
                        window.HTMLTextAreaElement.prototype, 'value'
                    )?.set;
                    if (nativeInputValueSetter) {
                        nativeInputValueSetter.call(el, value);
                    } else {
                        el.value = value;
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                }
            };
            setVal('#name, input[name="name"]', userData.nom);
            setVal('#telephone, input[name="telephone"]', userData.telephone);
            setVal('#email, input[name="email"]', userData.email);
            setVal('#adresse, input[name="adresse"]', userData.adresse);
            setVal('#note, textarea[name="note"]', '');

            const especesRadio = document.querySelector('input[value="especes"]');
            if (especesRadio) {
                especesRadio.checked = true;
                especesRadio.click();
                especesRadio.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }
    """, user)
    time.sleep(1)

    print("  Formulaire rempli avec vos informations.")
    print("  Paiement : Especes a la livraison")

    confirmer = input("\n  Confirmer la commande ? (Y/N) : ").strip().upper()
    if confirmer != "Y":
        print("  Commande annulee.")
        return False

    submit_btn = page.query_selector(
        'button:has-text("Confirmer la commande"), '
        'button.btn-primary[type="submit"], '
        'button:has-text("Confirmer")'
    )
    if submit_btn:
        submit_btn.click()
        time.sleep(5)
        print("\n  === Commande envoyee ! ===")
        return True
    else:
        print("  Bouton de confirmation non trouve.")
        return False


def send_confirmation_email(user: dict, cart_items: list[dict], zone_name: str) -> None:
    """Envoie un email de confirmation de commande."""
    if not user.get("email"):
        print("  Pas d'email configure, envoi ignore.")
        return

    print(f"\n  Envoi de l'email de confirmation a {user['email']}...")

    corps = f"Bonjour {user['nom']},\n\n"
    corps += "Votre commande KFC a ete effectuee avec succes !\n\n"
    corps += f"Zone de livraison : {zone_name}\n"
    corps += f"Adresse : {user['adresse']}\n"
    corps += f"Telephone : {user['telephone']}\n\n"
    corps += "Articles commandes :\n"
    for item in cart_items:
        a = item["article"]
        q = item["quantite"]
        corps += f"  - {a['nom']} x{q}\n"
    corps += "\nPaiement : Especes a la livraison\n"
    corps += "\nMerci pour votre commande !\n"
    corps += "KFC Senegal - kfcsenegal.sn\n"

    msg = MIMEMultipart()
    msg["From"] = "KFC Senegal <noreply@kfcsenegal.sn>"
    msg["To"] = user["email"]
    msg["Subject"] = "KFC - Confirmation de commande"
    msg.attach(MIMEText(corps, "plain", "utf-8"))

    try:
        with smtplib.SMTP("localhost", 25, timeout=10) as server:
            server.sendmail(msg["From"], [user["Email"]], msg.as_string())
        print("  Email envoye !")
    except Exception as e:
        print(f"  Envoi email echoue (non bloquant) : {e}")
        print("  Votre commande a bien ete passee malgre l'echec de l'email.")


def display_menu(products: list[dict]) -> dict:
    """Affiche le menu de maniere lisible. Retourne le mapping index -> produit."""
    print("\n" + "=" * 60)
    print("  MENU KFC BOURGUIBA")
    print("=" * 60)

    categories = {}
    for p in products:
        cat = p.get("category", "Autre")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(p)

    idx = 1
    product_map = {}
    for cat, items in categories.items():
        print(f"\n  --- {cat.upper()} ---")
        for item in items:
            prix = item.get("prix", 0)
            if isinstance(prix, str):
                prix_display = prix
            else:
                prix_display = f"{int(prix):,} FCFA".replace(",", " ")
            print(f"  [{idx}] {item['nom']} - {prix_display}")
            product_map[str(idx)] = item
            idx += 1

    print(f"\n  Total : {len(products)} articles disponibles")
    print("=" * 60)
    return product_map


def parse_args() -> dict:
    """Parse les arguments en ligne de commande."""
    args = {"visible": False, "help": False}
    for arg in sys.argv[1:]:
        if arg in ("--visible", "-v"):
            args["visible"] = True
        elif arg in ("--help", "-h"):
            args["help"] = True
    return args


def main() -> None:
    """Fonction principale du script."""
    args = parse_args()

    if args["help"]:
        print("""
KFC Senegal - Commande Automatisee

Usage:
    uv run python kfc_order.py [OPTIONS]

Options:
    --visible, -v    Lance le navigateur en mode visible (non headless)
    --help, -h       Affiche cette aide

Description:
    Script de commande automatise pour KFC Senegal (kfcsenegal.sn).
    - Enregistre vos informations dans une base SQLite (une seule fois)
    - Zone par defaut : SICAP LIBERTE 1/4
    - Tapez 'lieu' pour changer de zone, 'nouveau' pour ajouter un profil
    - Affiche le menu et vous permet de choisir vos articles
    - Finalise la commande avec paiement en especes
    - Envoie un email de confirmation
        """)
        return

    print("\n" + "=" * 60)
    print("  KFC SENEGAL - Commande Automatisee")
    print("  Site : https://kfcsenegal.sn")
    print("=" * 60)

    user = setup_user()

    headless = not args["visible"]
    mode = "headless" if headless else "visible"
    print(f"\n  Demarrage du navigateur ({mode})...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            print("  Ouverture de la page KFC...")
            if not navigate_with_retry(page, RESTAURANT_URL):
                print("  Impossible d'acceder au site. Verifiez votre connexion.")
                return

            selected_zone = ask_zone(page, user)

            print(f"\n  Zone selectionnee : {selected_zone['name']}")
            print("  Selection de la zone sur le site...")
            select_zone_on_site(page, selected_zone["id"])
            print("  Zone configuree !")

            print("  Attente avant de charger le menu...")
            time.sleep(5)

            if not navigate_with_retry(page, RESTAURANT_DETAIL_URL):
                print("  Impossible de charger la page du restaurant.")
                return
            time.sleep(5)

            print("  Chargement du menu...")
            products = scrape_menu(page)

            if not products:
                print("  Tentative de chargement du menu via le DOM...")
                products = scrape_menu_from_dom(page)

            if not products:
                print("\n  Menu non detecte automatiquement.")
                print("  Le site charge le menu dynamiquement.")
                print("  Tentative via l'API des produits...")

                api_products = page.evaluate("""
                    async () => {
                        try {
                            const resp = await fetch('/api/produits', {
                                headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                                credentials: 'same-origin'
                            });
                            if (resp.ok) return await resp.json();
                            return null;
                        } catch(e) { return null; }
                    }
                """)
                if api_products:
                    products = api_products if isinstance(api_products, list) else api_products.get("data", [])

            if not products:
                print("\n  Impossible de charger le menu automatiquement.")
                print("  Le site utilise un rendu dynamique complexe.")
                print("  Essayez avec le mode visible :")
                print("    uv run python kfc_order.py --visible")
                return

            product_map = display_menu(products)

            cart_items = []
            continuer = True
            while continuer:
                choix_article = input("\n  Numero de l'article (ou 'lieu'/'nouveau') : ").strip()

                if choix_article.lower() == "lieu":
                    selected_zone = ask_zone(page, user)
                    print(f"\n  Nouvelle zone : {selected_zone['name']}")
                    print("  Selection de la zone sur le site...")
                    select_zone_on_site(page, selected_zone["id"])
                    time.sleep(5)
                    if not navigate_with_retry(page, RESTAURANT_DETAIL_URL):
                        print("  Impossible de recharger le menu.")
                        return
                    time.sleep(5)
                    products = scrape_menu(page) or scrape_menu_from_dom(page)
                    if products:
                        product_map = display_menu(products)
                    continue

                if choix_article.lower() == "nouveau":
                    print("\n  --- Nouveau profil ---")
                    user = _ask_user_info()
                    save_user(user["nom"], user["telephone"], user["email"], user["adresse"])
                    print(f"  Profil '{user['nom']}' enregistre !")
                    continue

                if choix_article not in product_map:
                    print("  Numero invalide.")
                    continue

                quantite_str = input("  Quantite (defaut: 1) : ").strip()
                quantite = int(quantite_str) if quantite_str.isdigit() else 1

                article = product_map[choix_article]
                print(f"\n  Ajout : {article['nom']} x{quantite}")

                success = add_to_cart_api(page, article["id"], quantite)
                if not success:
                    success = add_to_cart(page, article["id"], quantite)

                if success:
                    cart_items.append({"article": article, "quantite": quantite})
                    print("  Ajoute au panier !")
                else:
                    print("  Echec de l'ajout au panier.")

                autre = input("\n  Autre chose ? (Y/N) : ").strip().upper()
                if autre != "Y":
                    continuer = False

            if not cart_items:
                print("\n  Panier vide. Commande annulee.")
                return

            print("\n  --- Recapitulatif de la commande ---")
            for item in cart_items:
                a = item["article"]
                q = item["quantite"]
                print(f"  - {a['nom']} x{q}")
            print(f"  Zone : {selected_zone['name']}")
            print("-------------------------------------")

            if checkout(page, user):
                send_confirmation_email(user, cart_items, selected_zone["name"])
                print("  Verifiez votre boite mail pour la confirmation.")

        except PwTimeout:
            print("\n  Timeout : le site met trop de temps a repondre.")
        except KeyboardInterrupt:
            print("\n\n  Commande interrompue par l'utilisateur.")
        except Exception as e:
            print(f"\n  Erreur inattendue : {e}")
        finally:
            browser.close()
            print("\n  Navigateur ferme.")


if __name__ == "__main__":
    main()
