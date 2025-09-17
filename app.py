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

# ----- Helpers -----

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
    cmd_copy = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path, "-c", "copy", out_path]
    res = subprocess.run(cmd_copy, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if res.returncode == 0:
        return
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

def ytdlp_download_mp4(url: str, tmpdir: str, base: str, cookies_path: str | None = None) -> str:
    """Télécharge un MP4 fusionné via yt-dlp. Renvoie le chemin du MP4."""
    otemplate = os.path.join(tmpdir, base + ".%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "-o", otemplate,
        url,
    ]
    if cookies_path:
        cmd[1:1] = ["--cookies", cookies_path]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        raise RuntimeError("yt-dlp a échoué:\n" + proc.stdout.decode(errors="ignore"))
    mp4_path = os.path.join(tmpdir, base + ".mp4")
    if not os.path.exists(mp4_path):
        raise FileNotFoundError("MP4 non trouvé après yt-dlp")
    return mp4_path

# ----- UI -----

st.title("YouTube → MP4 (HD) + MP3 + WAV")

# Sidebar: contrôles Cloud
st.sidebar.header("Options d'accès")
default_oauth = str(os.getenv("USE_OAUTH", "0")).lower() in ("1", "true", "yes")
use_oauth = st.sidebar.checkbox("Activer OAuth", value=default_oauth)

proxy_url = st.sidebar.text_input("Proxy HTTP(S) (facultatif)", placeholder="http://user:pass@host:port")
enable_fallback = st.sidebar.checkbox("Activer le fallback yt-dlp (auto si 403)", value=True)
cookies_file = st.sidebar.file_uploader("cookies.txt pour yt-dlp (facultatif)", type=["txt"])

with st.sidebar.expander("Aide / Pourquoi 403 en Cloud"):
    st.markdown(
        """
En local l'IP est résidentielle : souvent OK. En Cloud, l'IP peut être filtrée (403).
Solutions possibles :
- Activer OAuth et compléter l'authentification (le code peut s'afficher dans les logs Cloud).
- Utiliser un proxy HTTP(S) (résidentiel) pour les requêtes.
- Activer le fallback `yt-dlp` et, si besoin, fournir un `cookies.txt` exporté de votre navigateur.
        """
    )

url = st.text_input("URL YouTube", placeholder="https://www.youtube.com/watch?v=XXXXXXXXXXX")

# Aperçu vidéo
if url.strip():
    st.video(url.strip())

# Un seul bouton qui fait tout
if st.button("Télécharger (MP4+MP3+WAV)"):
    if not ffmpeg_available():
        st.error("ffmpeg introuvable. Installez-le et relancez.")
    elif not url.strip():
        st.error("Veuillez entrer une URL.")
    else:
        try:
            # Tentative pytubefix (avec éventuellement proxy + OAuth)
            proxies = {"http": proxy_url, "https": proxy_url} if proxy_url.strip() else None
            with st.spinner("Analyse de la vidéo et authentification si nécessaire..."):
                yt = YouTube(
                    url.strip(),
                    use_oauth=use_oauth,
                    allow_oauth_cache=True,
                    proxies=proxies
                )
                base = sanitize(yt.title)

            with tempfile.TemporaryDirectory() as tmp:
                with st.spinner("Téléchargement des flux vidéo et audio..."):
                    v, a = pick_streams(yt)
                    if not v or not a:
                        raise RuntimeError("Flux vidéo/audio introuvables.")
                    v_path = v.download(output_path=tmp, filename=base + "_v")
                    a_path = a.download(output_path=tmp, filename=base + "_a")

                mp4_path = os.path.join(tmp, base + ".mp4")
                with st.spinner("Fusion vidéo+audio en MP4..."):
                    merge_to_mp4(v_path, a_path, mp4_path)

                mp3_path = os.path.join(tmp, base + ".mp3")
                wav_path = os.path.join(tmp, base + ".wav")
                with st.spinner("Génération MP3..."):
                    make_mp3(mp4_path, mp3_path)
                with st.spinner("Génération WAV..."):
                    make_wav(mp4_path, wav_path)

                with st.spinner("Préparation du fichier ZIP..."):
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
                        z.write(mp4_path, arcname=os.path.basename(mp4_path))
                        z.write(mp3_path, arcname=os.path.basename(mp3_path))
                        z.write(wav_path, arcname=os.path.basename(wav_path))
                    buf.seek(0)
                    st.session_state["zip_bytes"] = buf.getvalue()
                    st.session_state["zip_name"] = f"{base}.zip"

            st.success("Préparation terminée.")

        except Exception as e:
            msg = str(e)
            is_403 = ("403" in msg) or (isinstance(e, HTTPError) and getattr(e, "code", None) == 403)
            if enable_fallback and is_403:
                try:
                    with tempfile.TemporaryDirectory() as tmp:
                        base = sanitize("video")
                        # cookies optionnels pour yt-dlp
                        cookies_path = None
                        if cookies_file is not None:
                            cookies_path = os.path.join(tmp, "cookies.txt")
                            with open(cookies_path, "wb") as f:
                                f.write(cookies_file.read())

                        with st.spinner("Fallback yt-dlp: téléchargement MP4..."):
                            mp4_path = ytdlp_download_mp4(url.strip(), tmp, base, cookies_path)

                        mp3_path = os.path.join(tmp, base + ".mp3")
                        wav_path = os.path.join(tmp, base + ".wav")
                        with st.spinner("Génération MP3..."):
                            make_mp3(mp4_path, mp3_path)
                        with st.spinner("Génération WAV..."):
                            make_wav(mp4_path, wav_path)

                        with st.spinner("Préparation du fichier ZIP..."):
                            buf = io.BytesIO()
                            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
                                z.write(mp4_path, arcname=os.path.basename(mp4_path))
                                z.write(mp3_path, arcname=os.path.basename(mp3_path))
                                z.write(wav_path, arcname=os.path.basename(wav_path))
                            buf.seek(0)
                            st.session_state["zip_bytes"] = buf.getvalue()
                            st.session_state["zip_name"] = f"{base}.zip"

                    st.success("Préparation terminée via yt-dlp.")
                except Exception as e2:
                    st.error(f"Echec du fallback yt-dlp: {e2}")
            else:
                st.error(f"Erreur: {e}")

# Bouton de téléchargement unique, indépendant des reruns
if "zip_bytes" in st.session_state and "zip_name" in st.session_state:
    st.download_button(
        label="Télécharger le fichier zip",
        data=st.session_state["zip_bytes"],
        file_name=st.session_state["zip_name"],
        mime="application/zip",
        use_container_width=True,
    )

st.caption("Téléchargez uniquement des contenus pour lesquels vous avez les droits ou une autorisation.")
