#!/usr/bin/env python3
"""
KFC Senegal - Script de commande automatise.

Ce script automatise le processus de commande sur kfcsenegal.sn :
1. Enregistre les informations utilisateur dans SQLite (une seule fois)
2. Ouvre le site via Playwright (navigateur headless)
3. Selectionne la zone de livraison (SICAP LIBERTE par defaut)
4. Affiche le menu et permet d'ajouter des articles au panier
5. Finalise la commande avec paiement en especes

Commandes utiles pendant la commande :
    lieu    - Changer la zone de livraison
    nouveau - Ajouter un nouveau profil utilisateur

Usage:
    uv run python kfc_order.py [--visible]
"""

import sys
import time

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from database import (
    init_db, get_user, save_user, update_user, user_exists,
    save_order, get_last_order,
    DEFAULT_ZONE,
)

BASE_URL = "https://kfcsenegal.sn"
RESTAURANT_URL = f"{BASE_URL}/restaurant/kfc-bourguiba"
COMMANDE_URL = f"{BASE_URL}/commande"

ZONES_CACHE: list[dict] = []


def block_non_essential(route) -> None:
    """Block analytics, fonts, images to reduce requests and avoid rate limiting.
    Allows: document (HTML), xhr/fetch (API calls), and build assets (JS/CSS).
    """
    url = route.request.url
    rt = route.request.resource_type
    if "kfcsenegal.sn" in url:
        if rt in ("document", "xhr", "fetch"):
            route.continue_()
        elif "/build/assets/" in url:
            route.continue_()
        else:
            route.abort()
    else:
        route.abort()


def navigate_with_retry(page, url: str, max_retries: int = 3, wait_between: int = 30) -> bool:
    """Navigate to URL with retry on rate limiting (HTTP 429)."""
    for attempt in range(max_retries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)

            title = page.evaluate("() => document.title || ''")
            if "429" in title:
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


def wait_for_zone_selection(page, timeout: int = 30) -> bool:
    """Attend que le selecteur de zone soit disponible."""
    start = time.time()
    while time.time() - start < timeout:
        has_zone = page.evaluate('() => !!document.querySelector(\'select[name="zone"]\')')
        if has_zone:
            return True
        time.sleep(1)
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
            current_zone = {"id": user["zone_id"], "name": user["zone_name"]}
            user = _ask_user_info(preserve_zone=current_zone)
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
            user = _ask_user_info(preserve_zone={"id": user["zone_id"], "name": user["zone_name"]})
            save_user(user["nom"], user["telephone"], user["email"], user["adresse"],
                      user["zone_id"], user["zone_name"])
            print("  Nouveau profil enregistre !")
            return user

    print("\n  === Premier lancement ===")
    print("  Veuillez entrer vos informations pour la commande.\n")
    user = _ask_user_info()
    save_user(user["nom"], user["telephone"], user["email"], user["adresse"],
              user["zone_id"], user["zone_name"])
    print("\n  Informations enregistrees !")
    return user


def _ask_user_info(preserve_zone: dict | None = None) -> dict:
    """Demande les informations utilisateur (nom, telephone, email, adresse).
    Si preserve_zone est fourni, garde la zone existante.
    """
    while True:
        nom = input("  Nom complet : ").strip()
        telephone = input("  Telephone   : ").strip()
        email = input("  Email       : ").strip()
        adresse = input("  Adresse     : ").strip()
        if nom and telephone and adresse:
            break
        print("  ! Nom, telephone et adresse sont obligatoires.\n")
    zone = preserve_zone or DEFAULT_ZONE
    return {
        "nom": nom,
        "telephone": telephone,
        "email": email,
        "adresse": adresse,
        "zone_id": zone["id"],
        "zone_name": zone["name"],
    }


def get_zones(page) -> list[dict]:
    """Extrait les zones de livraison depuis la page."""
    global ZONES_CACHE
    if ZONES_CACHE:
        return ZONES_CACHE

    zones = page.evaluate("""
        () => {
            const options = document.querySelectorAll('select[name="zone"] option[value]');
            return Array.from(options).filter(opt => opt.value !== '' && !opt.disabled).map(opt => ({
                id: opt.value,
                name: opt.textContent.trim().replace(/\\s+/g, ' '),
                frais: parseInt(opt.getAttribute('data-frais') || '0')
            }));
        }
    """)
    ZONES_CACHE = zones
    return zones


def ask_zone(page, user: dict) -> dict:
    """Demande a l'utilisateur de choisir une zone de livraison.
    Recharge la page d'atterrissage pour lire les zones disponibles.
    """
    current_zone = {"id": user.get("zone_id", DEFAULT_ZONE["id"]),
                    "name": user.get("zone_name", DEFAULT_ZONE["name"])}

    print("\n  Chargement des zones de livraison...")
    if not navigate_with_retry(page, RESTAURANT_URL):
        print("  Impossible de charger la page des zones.")
        return current_zone

    if not wait_for_zone_selection(page, timeout=15):
        print("  Selecteur de zone non trouve.")
        return current_zone

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


def select_zone_on_site(page, zone_id: str) -> bool:
    """Selectionne une zone et soumet le formulaire GET.
    Retourne True si la selection a reussi.
    """
    page.evaluate("""
        (zoneId) => {
            const sel = document.querySelector('select[name="zone"]');
            if (sel) {
                sel.value = zoneId;
                sel.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }
    """, zone_id)
    time.sleep(1)

    with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
        page.evaluate("""() => document.querySelector('select[name="zone"]').closest("form").submit()""")
    time.sleep(3)

    title = page.evaluate("() => document.title || ''")
    return "429" not in title


def load_menu(page, zone_id: str | None = None) -> list[dict]:
    """Charge le menu depuis la page Restaurant/Show (Inertia props).
    Le flux est : page d'atterrissage -> zone -> cooldown -> page restaurant.
    """
    print("\n  Chargement de la page d'atterrissage...")
    if not navigate_with_retry(page, RESTAURANT_URL):
        print("  Impossible d'acceder au site.")
        return []

    if not wait_for_zone_selection(page, timeout=15):
        print("  Selecteur de zone non trouve.")
        return []

    target = zone_id or DEFAULT_ZONE["id"]
    print("  Selection de la zone de livraison...")
    if not select_zone_on_site(page, target):
        print("  Echec de la selection de zone.")

    print("  Attente de 60s pour le rate-limiter...")
    time.sleep(60)

    print("  Chargement du menu du restaurant...")
    if not navigate_with_retry(page, RESTAURANT_URL):
        print("  Impossible de charger le menu.")
        return []

    time.sleep(5)

    products = scrape_menu(page)
    if not products:
        print("  Le menu n'a pas pu etre charge.")
        print("  Essayez avec : uv run python kfc_order.py --visible")
        return products

    print("  Menu charge ! Attente de 30s avant les appels API...")
    time.sleep(30)
    return products


def scrape_menu(page) -> list[dict]:
    """Recupere les produits depuis les props Inertia de Restaurant/Show."""
    return page.evaluate("""
        () => {
            const appEl = document.querySelector('#app[data-page]');
            if (!appEl) return [];
            try {
                const pageData = JSON.parse(appEl.getAttribute('data-page'));
                if (pageData.component !== 'Restaurant/Show') return [];
                const categories = pageData.props?.categories || [];
                const products = [];
                for (const cat of categories) {
                    const items = cat.produits || [];
                    for (const item of items) {
                        products.push({
                            id: String(item.id),
                            nom: item.nom || '',
                            description: item.description || '',
                            prix: item.prix || 0,
                            category: cat.libelle || cat.nom || '',
                            disponible: item.disponible !== false,
                            complements: (item.complements || []).map(c => ({
                                id: c.id, nom: c.nom || c.libelle || '', prix: c.prix || 0
                            })),
                            supplements: (item.supplements || []).map(s => ({
                                id: s.id, nom: s.nom || s.libelle || '', prix: s.prix || 0
                            }))
                        });
                    }
                }
                return products;
            } catch(e) { return []; }
        }
    """)


def add_to_cart(page, product: dict, quantite: int = 1) -> bool:
    """Ajoute un produit au panier via l'API /api/panier/ajouter.
    En cas de rate-limit (429), attend 30s et reessai une fois.
    """
    for attempt in range(2):
        try:
            result = page.evaluate("""
                async ({produitId, quantite, complements, supplements}) => {
                    try {
                        const xsrf = document.cookie.split('; ')
                            .find(c => c.startsWith('XSRF-TOKEN='))
                            ?.split('=').slice(1).join('=');
                        const decodedXsrf = xsrf ? decodeURIComponent(xsrf) : '';

                        const payload = {
                            produit_id: parseInt(produitId),
                            quantite: quantite,
                            complements: complements || [],
                            supplements: supplements || []
                        };
                        const response = await fetch('/api/panier/ajouter', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-Requested-With': 'XMLHttpRequest',
                                'X-XSRF-TOKEN': decodedXsrf,
                                'Accept': 'application/json',
                            },
                            body: JSON.stringify(payload),
                            credentials: 'same-origin'
                        });
                        const text = await response.text();
                        try {
                            const data = JSON.parse(text);
                            return { ok: response.ok, status: response.status, data: data };
                        } catch {
                            return { ok: false, status: response.status, error: 'Rate-limite ou HTML recu' };
                        }
                    } catch(e) {
                        return { ok: false, error: e.message };
                    }
                }
            """, {
                "produitId": product["id"],
                "quantite": quantite,
                "complements": [],
                "supplements": [],
            })
            if result.get("ok"):
                return True
            if result.get("status") == 429 and attempt == 0:
                print("  Rate-limite (429). Attente de 30s...")
                time.sleep(30)
                continue
            msg = result.get("data", {}).get("message", result.get("error", ""))
            print(f"  API erreur ({result.get('status', '?')}): {msg}")
            return False
        except Exception as e:
            print(f"  Erreur ajout panier: {e}")
            return False
    return False


def checkout(page, user: dict) -> bool:
    """Navigue vers la page de commande et remplit le formulaire."""
    # Deverrouiller toutes les ressources pour que Vue se monte
    page.unroute("**/*")

    print("\n  Attente de 60s avant le checkout...")
    time.sleep(60)

    print("  Navigation vers la page de commande...")
    if not navigate_with_retry(page, COMMANDE_URL, wait_between=60):
        print("  Impossible de charger la page de commande.")
        return False
    time.sleep(5)

    # Attendre que le composant Commande/Index soit charge
    print("  Attente du chargement de la page...")
    for _ in range(20):
        ready = page.evaluate("""
            () => {
                const el = document.querySelector('#app[data-page]');
                if (!el) return false;
                try {
                    const d = JSON.parse(el.getAttribute('data-page'));
                    return d.component === 'Commande/Index';
                } catch(e) { return false; }
            }
        """)
        if ready:
            break
        time.sleep(3)

    # Attendre que le panier soit charge via API
    print("  Attente du chargement du panier...")
    for _ in range(20):
        cart_ok = page.evaluate("""
            () => {
                const body = document.body.innerText || '';
                return body.includes('Confirmer la commande') || body.includes('Total');
            }
        """)
        if cart_ok:
            break
        time.sleep(3)

    # Remplir le formulaire via JS pour trigger les v-model Vue
    page.evaluate("""
        (userData) => {
            const setInput = (selector, value) => {
                const el = document.querySelector(selector);
                if (el) {
                    const proto = el.tagName === 'TEXTAREA'
                        ? window.HTMLTextAreaElement.prototype
                        : window.HTMLInputElement.prototype;
                    const nativeSetter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                    nativeSetter.call(el, value);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
            };
            setInput('input[name="name"], #name', userData.nom);
            setInput('input[name="telephone"], #telephone', userData.telephone);
            setInput('input[name="email"], #email', userData.email);
            setInput('input[name="adresse"], #adresse', userData.adresse);
            setInput('textarea[name="note"], #note', '');

            const radios = document.querySelectorAll('input[value="especes"]');
            radios.forEach(r => {
                r.checked = true;
                r.click();
                r.dispatchEvent(new Event('change', { bubbles: true }));
            });
        }
    """, user)
    time.sleep(2)

    print("  Formulaire rempli.")
    print("  Paiement : Especes a la livraison")

    confirmer = input("\n  Confirmer la commande ? (Y/N) : ").strip().upper()
    if confirmer != "Y":
        print("  Commande annulee.")
        return False

    print("  Attente de 30s avant soumission...")
    time.sleep(30)

    # Cliquer "Confirmer la commande"
    submit_btn = page.query_selector('button:has-text("Confirmer la commande")')
    if not submit_btn:
        submit_btn = page.query_selector('button[type="submit"]')

    if submit_btn:
        submit_btn.click(force=True)

        try:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        except PwTimeout:
            pass

        time.sleep(3)

        title = page.evaluate("() => document.title || ''")
        if "429" in title:
            print("  Rate-limite. Attente de 60s et reessai...")
            time.sleep(60)
            submit_btn = page.query_selector('button:has-text("Confirmer la commande")')
            if not submit_btn:
                submit_btn = page.query_selector('button[type="submit"]')
            if submit_btn:
                submit_btn.click(force=True)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=30000)
                except PwTimeout:
                    pass
                time.sleep(3)

        current_url = page.url
        if "commande" not in current_url or "confirmation" in current_url or "merci" in current_url.lower():
            print("\n  === Commande envoyee ! ===")
            print(f"  URL finale : {current_url}")
            return True

        page_text = page.evaluate("() => document.body.innerText || ''")
        if "erreur" in page_text.lower() or "error" in page_text.lower():
            print("  Erreur detectee lors de la soumission.")
            return False

        print(f"\n  Commande soumise (verifier manuellement). URL : {current_url}")
        return True
    else:
        print("  Bouton non trouve.")
        buttons = page.evaluate("""
            () => Array.from(document.querySelectorAll('button'))
                .map(b => b.textContent.trim()).filter(t => t)
        """)
        print(f"  Boutons visibles : {buttons}")
        return False


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


def ask_reorder(product_map: dict) -> list[dict] | None:
    """Propose de refaire la derniere commande. Retourne les items ou None."""
    last = get_last_order()
    if not last:
        return None

    print("\n  --- Derniere commande ---")
    for item in last["items"]:
        print(f"  - {item['nom']} x{item['quantite']}")
    print(f"  Zone : {last['zone_name']}")
    print(f"  Date : {last['created_at'][:16]}")

    choix = input("\n  Refaire cette commande ? (Y/N) : ").strip().upper()
    if choix != "Y":
        return None

    nom_to_idx = {}
    for idx_str, prod in product_map.items():
        nom_to_idx[prod["nom"].lower()] = idx_str

    cart_items = []
    for item in last["items"]:
        nom_lower = item["nom"].lower()
        if nom_lower not in nom_to_idx:
            print(f"  '{item['nom']}' n'est plus dans le menu, ignore.")
            continue

        idx_str = nom_to_idx[nom_lower]
        article = product_map[idx_str]
        old_q = item["quantite"]

        q_input = input(f"  {item['nom']} (quantite precedente: {old_q}) : ").strip()
        if q_input == "":
            quantite = old_q
        elif q_input.isdigit() and int(q_input) > 0:
            quantite = int(q_input)
        else:
            print(f"  Quantite invalide, gardée a {old_q}.")
            quantite = old_q

        cart_items.append({"article": article, "quantite": quantite})

    return cart_items if cart_items else None


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
    - Recharge automatiquement votre derniere commande (modifiable)
    - Tapez 'lieu' pour changer de zone, 'nouveau' pour ajouter un profil
    - Affiche le menu et vous permet de choisir vos articles
    - Finalise la commande avec paiement en especes
    - Enregistre l'historique des commandes
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
        page.route("**/*", block_non_essential)

        try:
            selected_zone = {"id": user["zone_id"], "name": user["zone_name"]}
            print(f"\n  Zone : {selected_zone['name']}")

            products = load_menu(page, zone_id=selected_zone["id"])
            if not products:
                return

            product_map = display_menu(products)

            cart_items = ask_reorder(product_map)

            if cart_items is None:
                cart_items = []
                continuer = True
                while continuer:
                    choix_article = input("\n  Numero de l'article (ou 'lieu'/'nouveau') : ").strip()

                    if choix_article.lower() == "lieu":
                        selected_zone = ask_zone(page, user)
                        print(f"\n  Nouvelle zone : {selected_zone['name']}")
                        print("  Rechargement du menu...")
                        products = load_menu(page, zone_id=selected_zone["id"])
                        if products:
                            product_map = display_menu(products)
                        continue

                    if choix_article.lower() == "nouveau":
                        print("\n  --- Nouveau profil ---")
                        current_zone = {"id": user["zone_id"], "name": user["zone_name"]}
                        user = _ask_user_info(preserve_zone=current_zone)
                        save_user(user["nom"], user["telephone"], user["email"], user["adresse"],
                                  user["zone_id"], user["zone_name"])
                        print(f"  Profil '{user['nom']}' enregistre !")
                        continue

                    if choix_article not in product_map:
                        print("  Numero invalide.")
                        continue

                    quantite_str = input("  Quantite (defaut: 1) : ").strip()
                    quantite = int(quantite_str) if quantite_str.isdigit() and int(quantite_str) > 0 else 1

                    article = product_map[choix_article]
                    print(f"\n  Ajout : {article['nom']} x{quantite}")

                    success = add_to_cart(page, article, quantite)

                    if success:
                        cart_items.append({"article": article, "quantite": quantite})
                        print("  Ajoute au panier !")
                        time.sleep(10)
                    else:
                        print("  Echec de l'ajout au panier.")
                        print("  Attente de 30s avant de reessayer...")
                        time.sleep(30)

                    autre = input("\n  Autre chose ? (Y/N) : ").strip().upper()
                    if autre != "Y":
                        continuer = False

            if not cart_items:
                print("\n  Panier vide. Commande annulee.")
                return

            print("\n  --- Recapitulatif de la commande ---")
            total = 0
            for item in cart_items:
                a = item["article"]
                q = item["quantite"]
                prix = a.get("prix", 0)
                sous_total = prix * q
                total += sous_total
                print(f"  - {a['nom']} x{q} = {sous_total:,} FCFA".replace(",", " "))
            print(f"\n  Total : {total:,} FCFA".replace(",", " "))
            print(f"  Zone : {selected_zone['name']}")
            print("-------------------------------------")

            if checkout(page, user):
                save_order(cart_items, selected_zone["id"], selected_zone["name"])

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
