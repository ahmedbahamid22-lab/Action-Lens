
import io
import os
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv
from PIL import Image, ImageOps


# ==========================================
# CONFIGURATION
# ==========================================

# Charger la clé API depuis le fichier .env
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "").strip()

SERPAPI_IMAGE = "https://serpapi.com/image"
SERPAPI_SEARCH = "https://serpapi.com/search.json"

MAX_IMAGE_BYTES = 500_000

# Texte ajouté automatiquement à Google Lens
SEARCH_QUERY = "action prix"

# Nombre maximum de résultats affichés
MAX_RESULTS = 10


# ==========================================
# PREPARATION DE LA PHOTO
# ==========================================

def prepare_image(uploaded_file):
    """Convertir la photo en JPEG de moins de 500 Ko."""

    original = Image.open(uploaded_file)
    img = ImageOps.exif_transpose(original).convert("RGB")

    for max_dim in (1600, 1280, 1024, 800, 640):
        reduced = img.copy()

        reduced.thumbnail(
            (max_dim, max_dim),
            Image.Resampling.LANCZOS
        )

        for quality in (85, 75, 65, 55, 45):
            output = io.BytesIO()

            reduced.save(
                output,
                format="JPEG",
                quality=quality,
                optimize=True
            )

            data = output.getvalue()

            if len(data) < MAX_IMAGE_BYTES:
                return data

    raise ValueError(
        "Impossible de compresser la photo."
    )


# ==========================================
# COMMUNICATION AVEC SERPAPI
# ==========================================

def request_json(method, url, **kwargs):
    """Envoyer une requête et récupérer le JSON."""

    try:
        response = requests.request(
            method,
            url,
            timeout=60,
            **kwargs
        )
    except requests.RequestException as error:
        raise RuntimeError(
            "Impossible de contacter SerpApi : "
            + type(error).__name__
        ) from error

    try:
        result = response.json()
    except ValueError:
        raise RuntimeError(
            f"Réponse API invalide (HTTP {response.status_code})."
        )

    if result.get("error"):
        raise RuntimeError(str(result["error"]))

    if not response.ok:
        raise RuntimeError(
            f"Erreur SerpApi HTTP {response.status_code}."
        )

    return result


# ==========================================
# ENVOI DE LA PHOTO
# ==========================================

def upload_picture(jpeg_bytes):

    result = request_json(
        "POST",
        SERPAPI_IMAGE,
        data={
            "api_key": SERPAPI_API_KEY
        },
        files={
            "image": (
                "produit.jpg",
                jpeg_bytes,
                "image/jpeg"
            )
        }
    )

    image_id = result.get("image_id")

    if not image_id:
        raise RuntimeError(
            "Identifiant image manquant dans la réponse."
        )

    return image_id


# ==========================================
# RECHERCHE GOOGLE LENS
# ==========================================

def google_lens(image_id):
    """
    Recherche Google Lens avec le texte
    'action prix' ajouté automatiquement.
    """

    params = {
        "engine": "google_lens",
        "image_id": image_id,
        "type": "all",
        "hl": "fr",
        "country": "fr",
        "q": SEARCH_QUERY,
        "api_key": SERPAPI_API_KEY
    }

    return request_json(
        "GET",
        SERPAPI_SEARCH,
        params=params
    )


# ==========================================
# INTERFACE STREAMLIT
# ==========================================

st.set_page_config(
    page_title="Action Infos Prix",
    page_icon="📷",
    layout="centered"
)

st.title("📷 Action Infos Prix")

st.write(
    "prend un photo  de l'article pour trouver son code" 
)

st.caption(
    f"Recherche automatique : {SEARCH_QUERY}"
)


# Vérification de la clé API
if not SERPAPI_API_KEY:
    st.error(
        "Clé API introuvable. Vérifie ton fichier .env."
    )
    st.stop()


# ==========================================
# CHOIX DE LA PHOTO
# ==========================================

method = st.radio(
    "Source de la photo",
    ["Importer une photo", "Prendre une photo"],
    horizontal=True
)

if method == "Importer une photo":

    photo = st.file_uploader(
        "Choisir une image",
        type=["jpg", "jpeg", "png", "webp"]
    )

else:

    photo = st.camera_input(
        "Photographier le produit"
    )


# Affichage de la photo
if photo:
    st.image(
        photo,
        caption="Produit à rechercher",
        width=250
    )


# ==========================================
# LANCEMENT DE LA RECHERCHE
# ==========================================

if st.button(
    "🔍 Rechercher l'article",
    type="primary",
    use_container_width=True
):

    if not photo:
        st.warning("Ajoute d'abord une photo.")

    else:
        try:

            with st.spinner(
                "Recherche de l'article en cours..."
            ):

                # 1. Préparation
                img_bytes = prepare_image(photo)

                # 2. Envoi à SerpApi
                image_id = upload_picture(img_bytes)

                # 3. Recherche avec "action prix"
                response = google_lens(image_id)

            # ==================================
            # EXTRACTION DES RESULTATS
            # ==================================

            visual_matches = response.get(
                "visual_matches", []
            )

            exact_matches = response.get(
                "exact_matches", []
            )

            results = (
                visual_matches
                if visual_matches
                else exact_matches
            )

            # ==================================
            # AFFICHAGE
            # ==================================

            if results:

                st.success(
                    f"{len(results)} résultats trouvés !"
                )

                for i, item in enumerate(
                    results[:MAX_RESULTS], 1
                ):

                    st.subheader(
                        f"{i}. "
                        f"{item.get('title') or 'Produit'}"
                    )

                    image_url = (
                        item.get("thumbnail")
                        or item.get("image")
                    )

                    if image_url:
                        st.image(
                            image_url,
                            width=180
                        )

                    st.write(
                        "Source :",
                        item.get("source", "Inconnue")
                    )

                    link = item.get("link")

                    if link:
                        st.link_button(
                            "🌐 Voir le produit",
                            link
                        )

                    st.divider()

            else:

                st.warning(
                    "Aucun résultat trouvé. "
                    "Essaie une autre photo."
                )

            # ==================================
            # DIAGNOSTIC API
            # ==================================

            with st.expander("🔧 Diagnostic API"):

                metadata = response.get(
                    "search_metadata", {}
                )

                st.json({
                    "query": SEARCH_QUERY,
                    "status": metadata.get("status"),
                    "search_id": metadata.get("id"),
                    "visual_matches": len(visual_matches),
                    "exact_matches": len(exact_matches)
                })

        except Exception as error:

            st.error(
                f"Erreur pendant la recherche : {error}"
            )


st.caption(
    "Action Infos Prix - Ahmed"
)
