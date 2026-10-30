import io
import os
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests
import streamlit as st
import zxingcpp
from dotenv import load_dotenv
from PIL import Image, ImageOps

# Composant optionnel : ouvre en priorité la caméra arrière sur mobile.
try:
    from streamlit_back_camera_input import back_camera_input
except ImportError:
    back_camera_input = None


# ==========================================
# CONFIGURATION
# ==========================================

load_dotenv(Path(__file__).resolve().parent / ".env")


def get_api_key():
    """Clé API dans .env (PC) ou dans les Secrets Streamlit Cloud."""
    key = os.getenv("SERPAPI_API_KEY", "").strip()
    if key:
        return key
    try:
        return str(st.secrets.get("SERPAPI_API_KEY", "")).strip()
    except Exception:
        return ""


SERPAPI_API_KEY = get_api_key()
SERPAPI_IMAGE = "https://serpapi.com/image"
SERPAPI_SEARCH = "https://serpapi.com/search.json"
SEARCH_QUERY = "action prix"  # Texte interne, non affiché dans l'interface.
MAX_IMAGE_BYTES = 500_000
MAX_RESULTS = 10

# Mettre True pour afficher les résultats visuels détaillés.
SHOW_VISUAL_RESULTS = False
# À activer uniquement pendant les tests techniques.
SHOW_DIAGNOSTICS = False

ACTION_PATH = re.compile(
    r"^/(?:[a-z]{2}-[a-z]{2}/)?p/(\d+)(?:/|$)", re.IGNORECASE
)


# ==========================================
# RECHERCHE ET PREPARATION DE LA PHOTO
# ==========================================

def request_json(method, url, **kwargs):
    """Effectuer un appel à l'API et récupérer son résultat."""
    try:
        response = requests.request(method, url, timeout=(10, 120), **kwargs)
        response.raise_for_status()
        data = response.json()
    except requests.Timeout as exc:
        raise RuntimeError("La recherche prend trop de temps. Réessaie plus tard.") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Connexion impossible : {type(exc).__name__}") from exc
    except ValueError as exc:
        raise RuntimeError("Le service a renvoyé une réponse invalide.") from exc

    if not isinstance(data, dict):
        raise RuntimeError("Format de réponse inattendu.")
    if data.get("error"):
        # Ne pas afficher les détails techniques du fournisseur aux utilisateurs.
        raise RuntimeError(
            "La recherche n’a pas abouti. Réessaie avec une autre photo "
            "ou un autre code-barres."
        )
    return data


def prepare_image(photo):
    """Compresser l'image avant son envoi (moins de 500 Ko)."""
    image = ImageOps.exif_transpose(
        Image.open(io.BytesIO(photo.getvalue()))
    ).convert("RGB")

    for max_dim in (1600, 1280, 1024, 800, 640):
        reduced = image.copy()
        reduced.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        for quality in (85, 75, 65, 55, 45):
            output = io.BytesIO()
            reduced.save(output, format="JPEG", quality=quality, optimize=True)
            if output.tell() < MAX_IMAGE_BYTES:
                return output.getvalue()
    raise ValueError("Impossible de compresser cette photo.")


def upload_picture(jpeg_bytes):
    response = request_json(
        "POST", SERPAPI_IMAGE,
        data={"api_key": SERPAPI_API_KEY},
        files={"image": ("produit.jpg", jpeg_bytes, "image/jpeg")},
    )
    image_id = response.get("image_id")
    if not image_id:
        raise RuntimeError("L'envoi de la photo a échoué.")
    return image_id


def search_by_photo(image_id):
    """Recherche visuelle orientée vers les articles Action."""
    return request_json(
        "GET", SERPAPI_SEARCH,
        params={
            "engine": "google_lens",
            "image_id": image_id,
            "type": "all",
            "hl": "fr",
            "country": "fr",
            "q": SEARCH_QUERY,
            "api_key": SERPAPI_API_KEY,
        },
    )


@st.cache_data(ttl=4 * 3600, show_spinner=False)
def search_action_by_text(query):
    """Recherche textuelle utilisée uniquement après lecture d'un EAN."""
    return request_json(
        "GET", SERPAPI_SEARCH,
        params={
            "engine": "google",
            "q": query,
            "hl": "fr",
            "gl": "fr",
            "num": 20,
            "api_key": SERPAPI_API_KEY,
        },
    )


# ==========================================
# RESULTATS ACTION
# ==========================================

def extract_action_products(items, required_product_id=None):
    """Extraire les numéros présents dans les liens produits action.com."""
    products = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        link = item.get("link")
        if not isinstance(link, str):
            continue
        try:
            parsed = urlsplit(link)
            host = (parsed.hostname or "").lower()
        except ValueError:
            continue
        if parsed.scheme != "https" or host not in ("action.com", "www.action.com"):
            continue
        match = ACTION_PATH.match(parsed.path)
        if not match:
            continue
        product_id = match.group(1)
        if required_product_id and product_id != required_product_id:
            continue
        candidate = {
            "id": product_id,
            "title": item.get("title") or "Article Action",
            "link": link,
            "thumbnail": item.get("thumbnail") or item.get("image"),
            "fr": parsed.path.lower().startswith("/fr-fr/"),
        }
        if product_id not in products or (
            candidate["fr"] and not products[product_id]["fr"]
        ):
            products[product_id] = candidate
    return sorted(products.values(), key=lambda p: not p["fr"])


def show_action_products(products, label="Numéro d'article Action"):
    """Afficher les fiches Action avec leur numéro."""
    if not products:
        st.info("Aucune fiche produit Action trouvée pour cette recherche.")
        return

    if len(products) > 1:
        st.warning(
            "Plusieurs articles possibles : vérifie la photo, le modèle "
            "et la contenance avant de choisir un numéro."
        )

    for index, product in enumerate(products):
        if index == 0:
            st.subheader(label)
        else:
            st.divider()
        st.code(product["id"], language=None)
        st.write(product["title"])
        if product["thumbnail"]:
            try:
                st.image(product["thumbnail"], width=140)
            except Exception:
                pass
        st.link_button(
            "Voir la fiche sur Action.com",
            product["link"],
            use_container_width=True,
        )


def show_visual_results(results):
    """Détails facultatifs, masqués par défaut."""
    st.subheader("🔎 Autres résultats de la recherche")
    if not results:
        st.info("Aucun autre résultat trouvé.")
        return
    st.write(f"{len(results)} résultat(s) trouvés.")
    for i, item in enumerate(results[:MAX_RESULTS], 1):
        if not isinstance(item, dict):
            continue
        st.markdown(f"**{i}. {item.get('title') or 'Produit'}**")
        thumbnail = item.get("thumbnail") or item.get("image")
        if thumbnail:
            try:
                st.image(thumbnail, width=170)
            except Exception:
                pass
        st.caption(f"Source : {item.get('source') or 'Inconnue'}")
        link = item.get("link")
        if isinstance(link, str) and link.startswith(("https://", "http://")):
            st.link_button("🌐 Ouvrir le résultat", link)
        st.divider()


def action_search_link(value):
    """Lien de recherche de secours sur le site officiel."""
    return f"https://www.action.com/fr-fr/search/?q={quote(value)}"


# ==========================================
# CAMERA ET LECTURE DU CODE-BARRES
# ==========================================

def take_photo(label, key):
    """Priorité à la caméra arrière ; caméra native si composant absent."""
    if back_camera_input is not None:
        st.caption("Utilise la caméra arrière et touche l'image pour capturer.")
        try:
            return back_camera_input(height=390, width=360, key=key)
        except Exception:
            st.info("Caméra arrière indisponible : utilise la caméra standard.")

    st.caption("Sur téléphone, sélectionne la caméra arrière si nécessaire.")
    return st.camera_input(label, key=f"{key}_native")


def scan_barcodes(photo):
    """Lire les codes-barres EAN/UPC à partir d'une photo."""
    image = ImageOps.exif_transpose(
        Image.open(io.BytesIO(photo.getvalue()))
    ).convert("RGB")
    image.thumbnail((2800, 2800), Image.Resampling.LANCZOS)

    found = []
    for barcode in zxingcpp.read_barcodes(image):
        value = (barcode.text or "").strip()
        fmt = str(barcode.format)
        if any(x in fmt.lower() for x in ("qr", "datamatrix", "aztec", "pdf417")):
            continue
        if value.isdigit() and 8 <= len(value) <= 14:
            if value not in [item["value"] for item in found]:
                found.append({"value": value, "format": fmt})
    return found


# ==========================================
# INTERFACE
# ==========================================

st.set_page_config(page_title="Ahmed Infos Prix", page_icon="📷")
st.title("📷 Ahmed Infos Prix")
st.write("Retrouve rapidement un article et sa fiche sur Action.")

mode = st.radio(
    "Choisis une méthode :",
    ["📷 Recherche par photo", "🔢 Numéro Action", "▥ Scanner un code-barres"],
    index=0,  # Le mode photo s'affiche en premier.
)

# La saisie directe d'un numéro Action fonctionne sans API.
if not SERPAPI_API_KEY and mode != "🔢 Numéro Action":
    st.error("Service de recherche indisponible. Contacte l'administrateur.")
    st.stop()


# ==========================================
# MODE 1 : RECHERCHE PAR PHOTO
# ==========================================

if mode == "📷 Recherche par photo":
    st.caption("Recherche avec l'IA")

    method = st.radio(
        "Source de la photo",
        ["Prendre une photo", "Importer"],
        index=0,  # Caméra affichée par défaut.
        horizontal=True,
        key="photo_method",
    )

    if method == "Prendre une photo":
        photo = take_photo("Photographier l'article", "lens_back_cam")
    else:
        photo = st.file_uploader(
            "Importer une photo de l'article",
            type=["jpg", "jpeg", "png", "webp"],
            key="lens_file",
        )

    if photo is not None:
        st.image(photo, width=250)

    if st.button("🔍 Rechercher l'article", type="primary", use_container_width=True):
        if photo is None:
            st.warning("Prends ou importe une photo.")
        else:
            try:
                with st.spinner("Recherche intelligente en cours..."):
                    image_id = upload_picture(prepare_image(photo))
                    response = search_by_photo(image_id)

                visual = response.get("visual_matches") or []
                exact = response.get("exact_matches") or []
                results = visual if visual else exact
                products = extract_action_products(visual + exact)

                st.subheader("🏷️ Articles trouvés sur Action")
                if products:
                    show_action_products(products[:5])
                else:
                    st.info("Aucune référence Action trouvée dans cette recherche.")

                # Activer/désactiver les résultats détaillés ici seulement.
                if SHOW_VISUAL_RESULTS:
                    st.divider()
                    show_visual_results(results)

                if SHOW_DIAGNOSTICS:
                    with st.expander("🔧 Informations techniques"):
                        st.json({
                            "visual_matches": len(visual),
                            "exact_matches": len(exact),
                            "action_products": len(products),
                            "status": (response.get("search_metadata") or {}).get("status"),
                        })
            except (RuntimeError, OSError, ValueError) as exc:
                st.error(f"Erreur pendant la recherche : {exc}")


# ==========================================
# MODE 2 : NUMERO ACTION (SANS API)
# ==========================================

elif mode == "🔢 Numéro Action":
    st.write("Saisis le numéro de l'article Action.")
    article_code = st.text_input(
        "Numéro Action", placeholder="Ex : 2532939", max_chars=10
    ).strip()

    if article_code:
        if re.fullmatch(r"[0-9]{7}", article_code):
            url = f"https://www.action.com/fr-fr/p/{article_code}"
            st.link_button(
                "🛒 Voir l'article sur Action", url,
                type="primary", use_container_width=True,
            )
        else:
            st.warning("Le numéro Action doit contenir 7 chiffres.")


# ==========================================
# MODE 3 : SCANNER UN CODE-BARRES
# ==========================================

else:
    st.write("Photographie le code-barres en montrant toutes les barres.")
    scan_method = st.radio(
        "Source du code-barres", ["Prendre une photo", "Importer"],
        index=0, horizontal=True, key="scan_method",
    )

    if scan_method == "Prendre une photo":
        barcode_photo = take_photo("Photographier le code-barres", "barcode_back_cam")
    else:
        barcode_photo = st.file_uploader(
            "Importer l'image du code-barres",
            type=["jpg", "jpeg", "png", "webp"],
            key="barcode_file",
        )

    if barcode_photo is not None:
        st.image(barcode_photo, width=280)

    if st.button("▥ Lire et rechercher", type="primary", use_container_width=True):
        if barcode_photo is None:
            st.warning("Prends ou importe une photo du code-barres.")
        else:
            try:
                with st.spinner("Lecture du code-barres..."):
                    scanned = scan_barcodes(barcode_photo)

                if not scanned:
                    st.warning(
                        "Aucun code-barres lisible. Évite les reflets "
                        "et rapproche la caméra."
                    )
                else:
                    ean = scanned[0]["value"]
                    st.success("Code-barres lu !")
                    st.code(ean, language=None)
                    st.caption(f"Format détecté : {scanned[0]['format']}")

                    if len(scanned) > 1:
                        st.caption(
                            "Plusieurs codes détectés ; recherche du premier : "
                            + ", ".join(item["value"] for item in scanned)
                        )

                    with st.spinner("Recherche du produit chez Action..."):
                        response = search_action_by_text(f'site:action.com "{ean}"')
                        products = extract_action_products(
                            response.get("organic_results") or []
                        )

                    if products:
                        st.warning(
                            "Correspondance(s) possible(s) : vérifie "
                            "le produit avant de choisir un numéro."
                        )
                        show_action_products(
                            products[:5], label="Référence(s) Action possible(s)"
                        )
                    else:
                        st.info(
                            "Code-barres reconnu, mais aucune fiche Action "
                            "trouvée automatiquement pour ce code."
                        )
                        st.link_button(
                            "Chercher ce code sur Action",
                            action_search_link(ean),
                            use_container_width=True,
                        )
            except (RuntimeError, OSError, ValueError) as exc:
                st.error(f"Erreur de lecture ou de recherche : {exc}")

st.caption("Ahmed Infos Prix — Prototype indépendant d'Action")
