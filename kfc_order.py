#!/usr/bin/env python3
"""
KFC Senegal - Automated ordering script.

Automates the ordering process on kfcsenegal.sn:
1. Saves user info to SQLite (once)
2. Opens the site via Playwright (headless browser)
3. Selects delivery zone (SICAP LIBERTE by default)
4. Displays the menu and adds items to cart
5. Finalizes the order with cash payment

Available commands during ordering:
    lieu    - Change delivery zone
    nouveau - Add a new user profile

Usage:
    uv run python kfc_order.py [--visible]
"""

import sys
import time
from collections import defaultdict

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


# ── Helpers ────────────────────────────────────────────────────────────


def format_fcfa(amount) -> str:
    """Format a number as 'X XXX FCFA'."""
    if isinstance(amount, str):
        return amount
    return f"{int(amount):,} FCFA".replace(",", " ")


def block_non_essential(route) -> None:
    """Block analytics, fonts, images to reduce requests and avoid rate limiting.
    Allows: document (HTML), xhr/fetch (API calls), and build assets (JS/CSS).
    """
    url = route.request.url
    rt = route.request.resource_type
    if "kfcsenegal.sn" in url:
        if rt in ("document", "xhr", "fetch") or "/build/assets/" in url:
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
                    print(f"  Rate-limited (429). Waiting {wait_between}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_between)
                    continue
                else:
                    print("  Too many requests. Try again later.")
                    return False
            return True
        except PwTimeout:
            if attempt < max_retries - 1:
                time.sleep(wait_between)
                continue
            return False
    return False


def wait_for_element(page, check_js: str, label: str, timeout: int = 60, interval: int = 3) -> bool:
    """Wait for a JS condition to become true. Returns True on success."""
    print(f"  Waiting for {label}...")
    start = time.time()
    while time.time() - start < timeout:
        if page.evaluate(check_js):
            return True
        time.sleep(interval)
    return False


def wait_for_zone_selection(page, timeout: int = 30) -> bool:
    """Wait for the zone selector to be available."""
    return wait_for_element(
        page,
        '() => !!document.querySelector(\'select[name="zone"]\')',
        "zone selector",
        timeout=timeout,
        interval=1,
    )


def find_submit_button(page):
    """Find the order confirmation button with fallback."""
    btn = page.query_selector('button:has-text("Confirmer la commande")')
    return btn or page.query_selector('button[type="submit"]')


def click_submit(page) -> bool:
    """Click the submit button and wait for page load. Returns True on success."""
    btn = find_submit_button(page)
    if not btn:
        return False
    btn.click(force=True)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30000)
    except PwTimeout:
        pass
    time.sleep(3)
    return True


# ── User management ────────────────────────────────────────────────────


def _ask_user_info(preserve_zone: dict | None = None) -> dict:
    """Prompt for user info (name, phone, email, address).
    If preserve_zone is provided, keeps the existing zone.
    """
    while True:
        nom = input("  Full name    : ").strip()
        telephone = input("  Phone        : ").strip()
        email = input("  Email        : ").strip()
        adresse = input("  Address      : ").strip()
        if nom and telephone and adresse:
            break
        print("  ! Name, phone and address are required.\n")
    zone = preserve_zone or DEFAULT_ZONE
    return {
        "nom": nom, "telephone": telephone, "email": email,
        "adresse": adresse, "zone_id": zone["id"], "zone_name": zone["name"],
    }


def setup_user() -> dict:
    """Handle user registration or retrieval.
    First run: asks for name, phone, email, address.
    Subsequent runs: shows info and asks for modification.
    Type 'nouveau' to add another profile.
    """
    init_db()

    if user_exists():
        user = get_user()
        print("\n  --- Your info ---")
        for key, label in [("nom", "Name"), ("telephone", "Phone"), ("email", "Email"), ("adresse", "Address"), ("zone_name", "Zone")]:
            print(f"  {label:12s}: {user[key]}")
        print("\n  (Y) Edit  (N) Keep  (other) New profile")
        choix = input("  > ").strip().upper()
        if choix == "Y":
            current_zone = {"id": user["zone_id"], "name": user["zone_name"]}
            user = _ask_user_info(preserve_zone=current_zone)
            update_user(nom=user["nom"], telephone=user["telephone"], email=user["email"], adresse=user["adresse"])
            print("  Info updated!")
            return user
        elif choix == "N":
            return user
        else:
            print("\n  --- New profile ---")
            user = _ask_user_info(preserve_zone={"id": user["zone_id"], "name": user["zone_name"]})
            save_user(user["nom"], user["telephone"], user["email"], user["adresse"], user["zone_id"], user["zone_name"])
            print("  Profile saved!")
            return user

    print("\n  === First run ===")
    print("  Enter your info for the order.\n")
    user = _ask_user_info()
    save_user(user["nom"], user["telephone"], user["email"], user["adresse"], user["zone_id"], user["zone_name"])
    print("\n  Info saved!")
    return user


# ── Zone management ────────────────────────────────────────────────────


def get_zones(page) -> list[dict]:
    """Extract delivery zones from the page."""
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
    """Ask user to pick a delivery zone. Reloads the landing page to read available zones."""
    current_zone = {
        "id": user.get("zone_id", DEFAULT_ZONE["id"]),
        "name": user.get("zone_name", DEFAULT_ZONE["name"]),
    }

    print("\n  Loading delivery zones...")
    if not navigate_with_retry(page, RESTAURANT_URL):
        print("  Could not load zones page.")
        return current_zone

    if not wait_for_zone_selection(page, timeout=15):
        print("  Zone selector not found.")
        return current_zone

    zones = get_zones(page)
    if not zones:
        print("  No zones found. Using default.")
        return current_zone

    print(f"\n  Current zone: {current_zone['name']}")
    print("\n  Available KFC delivery zones:")
    for i, z in enumerate(zones, 1):
        marker = " <-- current" if z["id"] == current_zone["id"] else ""
        print(f"  [{i}] {z['name']} - Fee: {format_fcfa(z['frais'])}{marker}")

    choix = input("\n  Select zone (number): ").strip()
    try:
        selected = zones[int(choix) - 1]
    except (ValueError, IndexError):
        print("  Invalid selection, keeping current zone.")
        return current_zone

    update_user(zone_id=selected["id"], zone_name=selected["name"])
    return selected


def select_zone_on_site(page, zone_id: str) -> bool:
    """Select a zone and submit the GET form. Returns True on success."""
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


# ── Menu ───────────────────────────────────────────────────────────────


def load_menu(page, zone_id: str | None = None) -> list[dict]:
    """Load menu from Restaurant/Show page (Inertia props).
    Flow: landing page -> zone -> cooldown -> restaurant page.
    """
    print("\n  Loading landing page...")
    if not navigate_with_retry(page, RESTAURANT_URL):
        print("  Cannot access site.")
        return []

    if not wait_for_zone_selection(page, timeout=15):
        print("  Zone selector not found.")
        return []

    target = zone_id or DEFAULT_ZONE["id"]
    print("  Selecting delivery zone...")
    if not select_zone_on_site(page, target):
        print("  Zone selection failed.")

    print("  Waiting 30s for rate-limiter...")
    time.sleep(30)

    print("  Loading restaurant menu...")
    if not navigate_with_retry(page, RESTAURANT_URL):
        print("  Could not load menu.")
        return []

    time.sleep(5)

    products = scrape_menu(page)
    if not products:
        print("  Menu could not be loaded.")
        print("  Try with: uv run python kfc_order.py --visible")
        return products

    print("  Menu loaded! Waiting 30s before API calls...")
    time.sleep(30)
    return products


def scrape_menu(page) -> list[dict]:
    """Get products from Inertia props on Restaurant/Show."""
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
                    for (const item of (cat.produits || [])) {
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


def display_menu(products: list[dict]) -> dict:
    """Display the menu in a readable format. Returns index -> product mapping."""
    print("\n" + "=" * 60)
    print("  MENU KFC BOURGUIBA")
    print("=" * 60)

    categories = defaultdict(list)
    for p in products:
        categories[p.get("category", "Other")].append(p)

    idx = 1
    product_map = {}
    for cat, items in categories.items():
        print(f"\n  --- {cat.upper()} ---")
        for item in items:
            print(f"  [{idx}] {item['nom']} - {format_fcfa(item.get('prix', 0))}")
            product_map[str(idx)] = item
            idx += 1

    print(f"\n  Total: {len(products)} items available")
    print("=" * 60)
    return product_map


# ── Cart ───────────────────────────────────────────────────────────────


def add_to_cart(page, product: dict, quantite: int = 1) -> bool:
    """Add a product to cart via /api/panier/ajouter.
    On rate-limit (429), waits 30s and retries once.
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
                            return { ok: false, status: response.status, error: 'Rate-limited or HTML received' };
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
                print("  Rate-limited (429). Waiting 30s...")
                time.sleep(30)
                continue
            msg = result.get("data", {}).get("message", result.get("error", ""))
            print(f"  API error ({result.get('status', '?')}): {msg}")
            return False
        except Exception as e:
            print(f"  Cart error: {e}")
            return False
    return False


# ── Checkout ───────────────────────────────────────────────────────────


def checkout(page, user: dict) -> bool:
    """Navigate to the order page and fill the form."""
    page.unroute("**/*")

    print("\n  Waiting 30s before checkout...")
    time.sleep(30)

    print("  Navigating to order page...")
    if not navigate_with_retry(page, COMMANDE_URL, wait_between=60):
        print("  Could not load order page.")
        return False
    time.sleep(5)

    wait_for_element(page, """
        () => {
            const el = document.querySelector('#app[data-page]');
            if (!el) return false;
            try { return JSON.parse(el.getAttribute('data-page')).component === 'Commande/Index'; }
            catch(e) { return false; }
        }
    """, "page component", timeout=60)

    wait_for_element(page, """
        () => {
            const body = document.body.innerText || '';
            return body.includes('Confirmer la commande') || body.includes('Total');
        }
    """, "cart content", timeout=60)

    # Fill form via JS to trigger Vue v-model
    page.evaluate("""
        (userData) => {
            const setInput = (selector, value) => {
                const el = document.querySelector(selector);
                if (el) {
                    const proto = el.tagName === 'TEXTAREA'
                        ? window.HTMLTextAreaElement.prototype
                        : window.HTMLInputElement.prototype;
                    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
            };
            setInput('input[name="name"], #name', userData.nom);
            setInput('input[name="telephone"], #telephone', userData.telephone);
            setInput('input[name="email"], #email', userData.email);
            setInput('input[name="adresse"], #adresse', userData.adresse);
            setInput('textarea[name="note"], #note', '');

            document.querySelectorAll('input[value="especes"]').forEach(r => {
                r.checked = true;
                r.click();
                r.dispatchEvent(new Event('change', { bubbles: true }));
            });
        }
    """, user)
    time.sleep(2)

    print("  Form filled.")
    print("  Payment: Cash on delivery")

    if input("\n  Confirm order? (Y/N): ").strip().upper() != "Y":
        print("  Order cancelled.")
        return False

    print("  Waiting 15s before submission...")
    time.sleep(15)

    if not click_submit(page):
        print("  Button not found.")
        buttons = page.evaluate("() => Array.from(document.querySelectorAll('button')).map(b => b.textContent.trim()).filter(t => t)")
        print(f"  Visible buttons: {buttons}")
        return False

    # Retry on rate-limit
    if "429" in page.evaluate("() => document.title || ''"):
        print("  Rate-limited. Waiting 60s and retrying...")
        time.sleep(60)
        click_submit(page)

    current_url = page.url
    if "merci" in current_url.lower() or "confirmation" in current_url or "commande" not in current_url:
        print("\n  === Order placed! ===")
        print(f"  Final URL: {current_url}")
        return True

    page_text = page.evaluate("() => document.body.innerText || ''")
    if "erreur" in page_text.lower() or "error" in page_text.lower():
        print("  Error detected during submission.")
        return False

    print(f"\n  Order submitted (verify manually). URL: {current_url}")
    return True


# ── Reorder ────────────────────────────────────────────────────────────


def ask_reorder(product_map: dict) -> list[dict] | None:
    """Offer to reorder the last order. Returns items or None."""
    last = get_last_order()
    if not last:
        return None

    print("\n  --- Last order ---")
    for item in last["items"]:
        print(f"  - {item['nom']} x{item['quantite']}")
    print(f"  Zone: {last['zone_name']}")
    print(f"  Date: {last['created_at'][:16]}")

    if input("\n  Reorder? (Y/N): ").strip().upper() != "Y":
        return None

    nom_to_idx = {prod["nom"].lower(): idx for idx, prod in product_map.items()}

    cart_items = []
    for item in last["items"]:
        nom_lower = item["nom"].lower()
        if nom_lower not in nom_to_idx:
            print(f"  '{item['nom']}' is no longer on the menu, skipped.")
            continue

        article = product_map[nom_to_idx[nom_lower]]
        old_q = item["quantite"]
        q_input = input(f"  {item['nom']} (previous qty: {old_q}): ").strip()
        quantite = old_q if q_input == "" else (int(q_input) if q_input.isdigit() and int(q_input) > 0 else old_q)
        cart_items.append({"article": article, "quantite": quantite})

    return cart_items if cart_items else None


# ── Ordering loop ──────────────────────────────────────────────────────


def ordering_loop(page, user: dict, product_map: dict, selected_zone: dict) -> tuple[list[dict], dict, dict]:
    """Interactive ordering loop. Returns (cart_items, selected_zone, user)."""
    cart_items = ask_reorder(product_map)
    if cart_items is not None:
        return cart_items, selected_zone, user

    cart_items = []
    while True:
        choix = input("\n  Item number (or 'lieu'/'nouveau'): ").strip()

        if choix.lower() == "lieu":
            selected_zone = ask_zone(page, user)
            print(f"\n  New zone: {selected_zone['name']}")
            print("  Reloading menu...")
            products = load_menu(page, zone_id=selected_zone["id"])
            if products:
                product_map = display_menu(products)
            continue

        if choix.lower() == "nouveau":
            print("\n  --- New profile ---")
            user = _ask_user_info(preserve_zone={"id": user["zone_id"], "name": user["zone_name"]})
            save_user(user["nom"], user["telephone"], user["email"], user["adresse"], user["zone_id"], user["zone_name"])
            print(f"  Profile '{user['nom']}' saved!")
            continue

        if choix not in product_map:
            print("  Invalid number.")
            continue

        q_str = input("  Quantity (default: 1): ").strip()
        quantite = int(q_str) if q_str.isdigit() and int(q_str) > 0 else 1

        article = product_map[choix]
        print(f"\n  Adding: {article['nom']} x{quantite}")

        if add_to_cart(page, article, quantite):
            cart_items.append({"article": article, "quantite": quantite})
            print("  Added to cart!")
            time.sleep(5)
        else:
            print("  Failed to add to cart.")
            print("  Waiting 30s before retrying...")
            time.sleep(30)

        if input("\n  Add another? (Y/N): ").strip().upper() != "Y":
            break

    return cart_items, selected_zone, user


# ── Args ───────────────────────────────────────────────────────────────


def parse_args() -> dict:
    """Parse command line arguments."""
    args = {"visible": False, "help": False}
    for arg in sys.argv[1:]:
        if arg in ("--visible", "-v"):
            args["visible"] = True
        elif arg in ("--help", "-h"):
            args["help"] = True
    return args


# ── Main ───────────────────────────────────────────────────────────────


def main() -> None:
    """Main entry point."""
    args = parse_args()

    if args["help"]:
        print("""
KFC Senegal - Automated Ordering

Usage:
    uv run python kfc_order.py [OPTIONS]

Options:
    --visible, -v    Launch browser in visible mode (non-headless)
    --help, -h       Show this help

Description:
    Automated ordering script for KFC Senegal (kfcsenegal.sn).
    - Saves your info in a SQLite database (once)
    - Default zone: SICAP LIBERTE 1/4
    - Auto-reloads your last order (editable)
    - Type 'lieu' to change zone, 'nouveau' to add a profile
    - Displays menu and lets you pick items
    - Finalizes order with cash payment
    - Saves order history
        """)
        return

    print("\n" + "=" * 60)
    print("  KFC SENEGAL - Automated Ordering")
    print("  Site: https://kfcsenegal.sn")
    print("=" * 60)

    user = setup_user()

    headless = not args["visible"]
    print(f"\n  Starting browser ({'headless' if headless else 'visible'})...")

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
            print(f"\n  Zone: {selected_zone['name']}")

            products = load_menu(page, zone_id=selected_zone["id"])
            if not products:
                return

            product_map = display_menu(products)
            cart_items, selected_zone, user = ordering_loop(page, user, product_map, selected_zone)

            if not cart_items:
                print("\n  Cart empty. Order cancelled.")
                return

            print("\n  --- Order summary ---")
            total = sum(item["article"].get("prix", 0) * item["quantite"] for item in cart_items)
            for item in cart_items:
                a, q = item["article"], item["quantite"]
                print(f"  - {a['nom']} x{q} = {format_fcfa(a.get('prix', 0) * q)}")
            print(f"\n  Total: {format_fcfa(total)}")
            print(f"  Zone: {selected_zone['name']}")
            print("-" * 40)

            if checkout(page, user):
                save_order(cart_items, selected_zone["id"], selected_zone["name"])

        except PwTimeout:
            print("\n  Timeout: site took too long to respond.")
        except KeyboardInterrupt:
            print("\n\n  Order interrupted by user.")
        except Exception as e:
            print(f"\n  Unexpected error: {e}")
        finally:
            browser.close()
            print("\n  Browser closed.")


if __name__ == "__main__":
    main()
