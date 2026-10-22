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


# ==========================================
# CONFIGURATION
# ==========================================

load_dotenv(Path(__file__).resolve().parent / ".env")


def get_api_key():
    """Clé API locale (.env) ou Streamlit Cloud (Secrets)."""
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
SEARCH_QUERY = "action prix"
MAX_IMAGE_BYTES = 500_000
MAX_RESULTS = 10

# Exemple de fiche : /fr-fr/p/2532939/eau-de-toilette-adidas-ice-dive/
ACTION_PATH = re.compile(
    r"^/(?:[a-z]{2}-[a-z]{2}/)?p/(\d+)(?:/|$)", re.IGNORECASE
)


# ==========================================
# RECHERCHES SERPAPI
# ==========================================

def request_json(method, url, **kwargs):
    """Appel HTTP commun aux recherches image et texte."""
    try:
        response = requests.request(
            method, url, timeout=(10, 120), **kwargs
        )
        response.raise_for_status()
        data = response.json()
    except requests.Timeout as exc:
        raise RuntimeError(
            "SerpApi met trop de temps à répondre. Réessaie plus tard."
        ) from exc
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Connexion à SerpApi impossible : {type(exc).__name__}"
        ) from exc
    except ValueError as exc:
        raise RuntimeError("SerpApi a renvoyé une réponse non JSON.") from exc

    if not isinstance(data, dict):
        raise RuntimeError("Format de réponse SerpApi inattendu.")
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data


def prepare_image(uploaded_file):
    """Réduire la photo pour l'envoi à SerpApi (< 500 Ko)."""
    img = ImageOps.exif_transpose(
        Image.open(io.BytesIO(uploaded_file.getvalue()))
    ).convert("RGB")

    for max_dim in (1600, 1280, 1024, 800, 640):
        reduced = img.copy()
        reduced.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        for quality in (85, 75, 65, 55, 45):
            output = io.BytesIO()
            reduced.save(output, format="JPEG", quality=quality, optimize=True)
            if output.tell() < MAX_IMAGE_BYTES:
                return output.getvalue()

    raise ValueError("Impossible de compresser cette photo.")


def upload_picture(jpeg_bytes):
    data = request_json(
        "POST",
        SERPAPI_IMAGE,
        data={"api_key": SERPAPI_API_KEY},
        files={"image": ("produit.jpg", jpeg_bytes, "image/jpeg")},
    )
    image_id = data.get("image_id")
    if not image_id:
        raise RuntimeError("SerpApi n'a pas fourni d'identifiant d'image.")
    return image_id


def google_lens(image_id):
    """Recherche par photo orientée automatiquement vers Action."""
    return request_json(
        "GET",
        SERPAPI_SEARCH,
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
def google_search_action(query):
    """Recherche Google textuelle ; résultat mis en cache pendant 4 h."""
    return request_json(
        "GET",
        SERPAPI_SEARCH,
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
# EXTRACTION DES LIENS OFFICIELS ACTION
# ==========================================

def extract_action_products(items, required_product_id=None):
    """Ne conserver que les véritables URL de produits action.com."""
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

        if parsed.scheme != "https" or host not in {
            "action.com", "www.action.com"
        }:
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


def show_action_products(products, label="Référence Action"):
    """Mettre la référence Action en évidence, avec sa fiche officielle."""
    if not products:
        st.info("Aucune fiche produit Action confirmée dans les résultats.")
        return

    if len(products) > 1:
        st.warning(
            "Plusieurs articles possibles : compare la photo, le modèle "
            "et la contenance avant de choisir."
        )

    for index, product in enumerate(products):
        if index == 0:
            st.subheader(label)
        else:
            st.markdown("---")

        st.code(product["id"], language=None)
        st.write(product["title"])
        if product["thumbnail"]:
            try:
                st.image(product["thumbnail"], width=140)
            except Exception:
                pass  # Une image distante peut ne plus être disponible.
        st.link_button(
            "Voir la fiche sur Action.com",
            product["link"],
            use_container_width=True,
        )


def show_visual_results(results):
    """Afficher les 10 premiers résultats classiques de Google Lens."""
    st.subheader("🔎 Résultats Google Lens")
    if not results:
        st.info("Aucun résultat visuel trouvé.")
        return

    st.write(f"{len(results)} résultat(s) renvoyé(s).")
    for i, item in enumerate(results[:MAX_RESULTS], 1):
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
    """Recherche de secours sur le site officiel (pas de clé API)."""
    return f"https://www.action.com/fr-fr/search/?q={quote(value)}"


# ==========================================
# LECTURE D'UN CODE-BARRES
# ==========================================

def scan_barcodes(uploaded_file):
    """Scanner EAN/UPC et autres codes-barres numériques sur une photo."""
    image = ImageOps.exif_transpose(
        Image.open(io.BytesIO(uploaded_file.getvalue()))
    ).convert("RGB")

    # Limiter la taille pour éviter de ralentir le téléphone/serveur.
    image.thumbnail((2800, 2800), Image.Resampling.LANCZOS)

    found = []
    for barcode in zxingcpp.read_barcodes(image):
        text = (barcode.text or "").strip()
        fmt = str(barcode.format)
        # Exclure QR codes et autres codes 2D : ce mode cible les barres.
        if any(x in fmt.lower() for x in ("qr", "datamatrix", "aztec", "pdf417")):
            continue
        if text.isdigit() and 8 <= len(text) <= 14:
            if text not in [b["value"] for b in found]:
                found.append({"value": text, "format": fmt})

    return found


# ==========================================
# INTERFACE STREAMLIT
# ==========================================

st.set_page_config(page_title="Action Infos Prix", page_icon="📷")
st.title("📷 Action Infos Prix")
st.write("Retrouver rapidement un article sur le site officiel Action.")

mode = st.radio(
    "Choisis une méthode :",
    ["📷 Recherche par photo", "🔢 Numéro Action", "▥ Scanner un code-barres"],
)

# La clé SerpApi n'est pas nécessaire pour le MODE 2
if not SERPAPI_API_KEY and mode != "🔢 Numéro Action":
    st.error("Clé SerpApi absente : vérifie .env ou les Secrets Streamlit.")
    st.stop()

# ==========================================
# MODE 1 : PHOTO + GOOGLE LENS
# ==========================================

if mode == "📷 Recherche par photo":
    st.caption(f"Recherche Google Lens : « {SEARCH_QUERY} »")

    method = st.radio(
        "Source de la photo", ["Importer", "Prendre une photo"],
        horizontal=True,
        key="photo_method",
    )
    if method == "Importer":
        photo = st.file_uploader(
            "Photo de l'article", type=["jpg", "jpeg", "png", "webp"],
            key="lens_file",
        )
    else:
        photo = st.camera_input("Photographier l'article", key="lens_cam")

    if photo:
        st.image(photo, width=250)

    if st.button("🔍 Rechercher l'article", type="primary", use_container_width=True):
        if not photo:
            st.warning("Ajoute d'abord une photo.")
        else:
            try:
                with st.spinner("Recherche Google Lens en cours..."):
                    image_id = upload_picture(prepare_image(photo))
                    response = google_lens(image_id)

                visual = response.get("visual_matches") or []
                exact = response.get("exact_matches") or []
                results = visual if visual else exact
                products = extract_action_products(visual + exact)

                st.subheader("🏷️ Articles trouvés sur Action")
                if products:
                    show_action_products(products[:5])
                else:
                    st.info("Aucune référence Action trouvée dans cette recherche.")
                st.divider()
                show_visual_results(results)

                with st.expander("🔧 Diagnostic"):
                    st.json({
                        "visual_matches": len(visual),
                        "exact_matches": len(exact),
                        "action_products": len(products),
                        "status": (response.get("search_metadata") or {}).get("status"),
                    })
            except (RuntimeError, OSError, ValueError) as exc:
                st.error(f"Erreur pendant la recherche : {exc}")


# ==========================================
# MODE 2 : SAISIE DU NUMERO ACTION
# ==========================================

elif mode == "🔢 Numéro Action":

    st.write("Saisis le numéro de l'article Action.")

    # Saisie du numéro
    article_code = st.text_input(
        "Numéro Action",
        placeholder="Ex : 2532939",
        max_chars=10
    ).strip()

    # Vérifier que le numéro contient 7 chiffres
    if article_code:

        if re.fullmatch(r"\d{7}", article_code):

            # Construire directement l'URL Action
            url = f"https://www.action.com/fr-fr/p/{article_code}"

            #st.success("Lien de l'article prêt !")

            # Ouvrir directement la fiche officielle
            st.link_button(
                "🛒 Voir l'article sur Action",
                url,
                type="primary",
                use_container_width=True
            )

        else:
            st.warning(
                "Le numéro Action doit contenir 7 chiffres."
            )


# ==========================================
# MODE 3 : CODE-BARRES (EAN / UPC)
# ==========================================

else:
    st.write(
        "Prends une photo nette du code-barres, avec toutes les barres visibles."
    )
    scan_method = st.radio(
        "Source du code-barres", ["Prendre une photo", "Importer"],
        horizontal=True,
        key="scan_method",
    )

    if scan_method == "Prendre une photo":
        barcode_photo = st.camera_input(
            "Photographier le code-barres", key="barcode_cam"
        )
    else:
        barcode_photo = st.file_uploader(
            "Image du code-barres", type=["jpg", "jpeg", "png", "webp"],
            key="barcode_file",
        )

    if barcode_photo:
        st.image(barcode_photo, width=280)

    if st.button("▥ Lire et rechercher", type="primary", use_container_width=True):
        if not barcode_photo:
            st.warning("Ajoute une photo du code-barres.")
        else:
            try:
                with st.spinner("Lecture du code-barres..."):
                    scanned = scan_barcodes(barcode_photo)

                if not scanned:
                    st.warning(
                        "Aucun code-barres numérique lisible. Rapproche-toi, "
                        "évite les reflets et garde les barres nettes."
                    )
                else:
                    ean = scanned[0]["value"]
                    st.success("Code-barres lu !")
                    st.code(ean, language=None)
                    st.caption(f"Format détecté : {scanned[0]['format']}")

                    if len(scanned) > 1:
                        st.caption(
                            "Plusieurs codes détectés ; recherche du premier : "
                            + ", ".join(x["value"] for x in scanned)
                        )

                    with st.spinner("Recherche du produit chez Action..."):
                        response = google_search_action(
                            f'site:action.com "{ean}"'
                        )
                        products = extract_action_products(
                            response.get("organic_results") or []
                        )

                    if products:
                        st.warning(
                            "Correspondance(s) possible(s) : vérifie bien "
                            "le produit sur chaque fiche."
                        )
                        show_action_products(
                            products[:5], label="Référence(s) Action possible(s)"
                        )
                    else:
                        st.info(
                            "Code-barres reconnu, mais aucune fiche Action "
                            "n'a été retrouvée automatiquement pour cet EAN/UPC."
                        )
                        st.link_button(
                            "Chercher ce code sur Action",
                            action_search_link(ean),
                            use_container_width=True,
                        )
            except (RuntimeError, OSError, ValueError) as exc:
                st.error(f"Erreur de lecture/recherche : {exc}")

st.caption("Action Infos Prix — Prototype indépendant d'Action — Ahmed")
