# python -m streamlit run app.py


import os
import io
import shutil
import subprocess
import tempfile
import zipfile
from urllib.error import HTTPError

import streamlit as st
from pytubefix import YouTube
from pytubefix.exceptions import VideoUnavailable

st.set_page_config(page_title="YouTube → MP4+MP3+WAV", layout="centered")

# ----------------- Helpers -----------------

def sanitize(name: str) -> str:
    name = (name or "video").strip()
    return "".join(c if c.isalnum() or c in " ._-()" else "_" for c in name)

def pick_streams(yt: YouTube):
    v = (yt.streams.filter(adaptive=True, only_video=True, file_extension="mp4")
                  .order_by("resolution").desc().first())
    if not v:
        v = yt.streams.filter(adaptive=True, only_video=True).order_by("resolution").desc().first()
    a = (yt.streams.filter(only_audio=True, file_extension="mp4")
                  .order_by("abr").desc().first())
    if not a:
        a = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
    return v, a

def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None

def merge_to_mp4(video_path: str, audio_path: str, out_path: str):
    # tentative sans ré-encodage
    cmd_copy = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path, "-c", "copy", out_path]
    res = subprocess.run(cmd_copy, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if res.returncode == 0:
        return
    # fallback H.264/AAC
    cmd_x264 = [
        "ffmpeg", "-y",
        "-i", video_path, "-i", audio_path,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        out_path
    ]
    subprocess.run(cmd_x264, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def make_mp3(mp4_path: str, mp3_path: str):
    cmd = ["ffmpeg", "-y", "-i", mp4_path, "-vn", "-c:a", "libmp3lame", "-q:a", "2", mp3_path]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def make_wav(mp4_path: str, wav_path: str):
    cmd = ["ffmpeg", "-y", "-i", mp4_path, "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", wav_path]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

@st.cache_data(show_spinner=False, ttl=3600)
def run_download_job(url: str, use_oauth: bool, proxy_url: str | None):
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg introuvable. Installez-le puis relancez.")
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    yt = YouTube(
        url.strip(),
        use_oauth=use_oauth,
        allow_oauth_cache=True,
        proxies=proxies
    )
    base = sanitize(yt.title)

    with tempfile.TemporaryDirectory() as tmp:
        v, a = pick_streams(yt)
        if not v or not a:
            raise RuntimeError("Flux vidéo/audio introuvables.")
        v_path = v.download(output_path=tmp, filename=base + "_v")
        a_path = a.download(output_path=tmp, filename=base + "_a")

        mp4_path = os.path.join(tmp, base + ".mp4")
        merge_to_mp4(v_path, a_path, mp4_path)

        mp3_path = os.path.join(tmp, base + ".mp3")
        make_mp3(mp4_path, mp3_path)

        wav_path = os.path.join(tmp, base + ".wav")
        make_wav(mp4_path, wav_path)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(mp4_path, arcname=os.path.basename(mp4_path))
            z.write(mp3_path, arcname=os.path.basename(mp3_path))
            z.write(wav_path, arcname=os.path.basename(wav_path))
        buf.seek(0)
        return buf.getvalue(), f"{base}.zip"

# ----------------- State init -----------------

if "run_job" not in st.session_state:
    st.session_state.run_job = False
if "processing" not in st.session_state:
    st.session_state.processing = False
if "result" not in st.session_state:
    st.session_state.result = None
if "error" not in st.session_state:
    st.session_state.error = None
if "last_params" not in st.session_state:
    st.session_state.last_params = None

# ----------------- UI -----------------

st.title("YouTube → MP4 (HD) + MP3 + WAV")

with st.form("form", clear_on_submit=False):
    url = st.text_input("URL YouTube", placeholder="https://www.youtube.com/watch?v=XXXXXXXXXXX")
    if url.strip():
        st.video(url.strip())

    c1, c2 = st.columns(2)
    with c1:
        default_oauth = str(os.getenv("USE_OAUTH", "0")).lower() in ("1", "true", "yes")
        use_oauth = st.checkbox("Activer OAuth", value=default_oauth)
    with c2:
        proxy_url = st.text_input("Proxy HTTP(S) (facultatif)", placeholder="http://user:pass@host:port")

    st.markdown(
        """
**Boucles évitées**
- Le téléchargement est encapsulé et mis en cache pour la paire (URL, OAuth, Proxy).
- Le bouton ne lance le traitement qu'une fois et un verrou interne empêche les relances.

**OAuth**
- À activer si la vidéo requiert connexion/âge. Sur certains hébergeurs, un code à saisir peut être demandé sur https://www.google.com/device.
        """
    )

    submit = st.form_submit_button("Préparer le téléchargement", disabled=st.session_state.processing)

# Armer l'exécution à la soumission
if submit:
    st.session_state.run_job = True
    st.session_state.processing = True
    st.session_state.result = None
    st.session_state.error = None
    st.session_state.last_params = (url.strip(), bool(use_oauth), proxy_url.strip() or None)

# Exécuter exactement une fois si armé
if st.session_state.run_job and st.session_state.processing and st.session_state.last_params:
    u, o, p = st.session_state.last_params
    if not u:
        st.session_state.error = "Veuillez entrer une URL."
        st.session_state.processing = False
        st.session_state.run_job = False
    else:
        try:
            with st.spinner("Préparation en cours..."):
                zip_bytes, zip_name = run_download_job(u, o, p)
            st.session_state.result = (zip_bytes, zip_name)
        except VideoUnavailable:
            st.session_state.error = "Vidéo indisponible. Vérifiez l'URL et les restrictions."
        except HTTPError as e:
            st.session_state.error = f"HTTPError {getattr(e, 'code', '?')}: {getattr(e, 'reason', '')}"
        except subprocess.CalledProcessError as e:
            st.session_state.error = f"Erreur FFmpeg: {e}"
        except Exception as e:
            st.session_state.error = f"Erreur: {e}"
        finally:
            # on désarme sans st.rerun()
            st.session_state.processing = False
            st.session_state.run_job = False

# Affichage résultat ou erreur (ne relance rien)
if st.session_state.result:
    zip_bytes, zip_name = st.session_state.result
    st.success("Préparation terminée.")
    st.download_button(
        "Télécharger le fichier zip",
        data=zip_bytes,
        file_name=zip_name,
        mime="application/zip",
        use_container_width=True,
    )

if st.session_state.error:
    st.error(st.session_state.error)
    st.info("Si cela persiste en Cloud: activez OAuth et/ou renseignez un proxy HTTP(S), ou testez en local.")

if st.button("Réinitialiser"):
    for k in ("run_job", "processing", "result", "error", "last_params"):
        st.session_state.pop(k, None)
