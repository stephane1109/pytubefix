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

# ------------- Helpers -------------

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
    # 1) tentative sans ré-encodage
    cmd_copy = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path, "-c", "copy", out_path]
    res = subprocess.run(cmd_copy, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if res.returncode == 0:
        return
    # 2) fallback: ré-encodage H.264/AAC pour garantir un MP4 lisible
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

def run_job(url: str, use_oauth: bool, proxy_url: str | None):
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    if not ffmpeg_available():
        raise RuntimeError("ffmpeg introuvable. Installez-le puis relancez.")

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
            return {
                "zip_bytes": buf.getvalue(),
                "zip_name": f"{base}.zip"
            }

# ------------- UI -------------

st.title("YouTube → MP4 (HD) + MP3 + WAV")

# Etat global pour contrôler les reruns
if "task" not in st.session_state:
    st.session_state.task = None          # dict avec url, oauth, proxy, status
if "result" not in st.session_state:
    st.session_state.result = None        # dict avec zip_bytes et zip_name
if "error" not in st.session_state:
    st.session_state.error = None

# Formulaire pour éviter les exécutions à chaque frappe
with st.form(key="dl_form", clear_on_submit=False):
    url = st.text_input("URL YouTube", placeholder="https://www.youtube.com/watch?v=XXXXXXXXXXX")
    if url.strip():
        st.video(url.strip())

    col1, col2 = st.columns(2)
    with col1:
        default_oauth = str(os.getenv("USE_OAUTH", "0")).lower() in ("1", "true", "yes")
        use_oauth = st.checkbox("Activer OAuth", value=default_oauth)
    with col2:
        proxy_url = st.text_input("Proxy HTTP(S) (facultatif)", placeholder="http://user:pass@host:port")

    st.markdown(
        """
**Quand activer OAuth**
- Vidéos avec restriction d'âge, non répertoriées accessibles via votre compte, membres-only.
- Sur hébergeurs Cloud, un code peut être demandé sur https://www.google.com/device.  
  Si vous ne pouvez pas le saisir depuis l'interface, désactivez OAuth ou exécutez en local.

**403 en Cloud**
- Fréquent car certaines IPs datacenter sont filtrées.
- Solutions côté pytubefix: activer OAuth et/ou renseigner un proxy HTTP(S).
        """
    )

    submitted = st.form_submit_button("Préparer le téléchargement")

# Une soumission crée une tâche une seule fois
if submitted:
    if not url.strip():
        st.error("Veuillez entrer une URL.")
    else:
        # initialise la tâche et efface l’ancien résultat
        st.session_state.task = {
            "url": url.strip(),
            "use_oauth": bool(use_oauth),
            "proxy_url": proxy_url.strip() or None,
            "status": "pending"
        }
        st.session_state.result = None
        st.session_state.error = None
        st.rerun()

# Exécuter la tâche exactement une fois
task = st.session_state.task
if task and task.get("status") == "pending":
    st.session_state.task["status"] = "running"
    try:
        res = run_job(task["url"], task["use_oauth"], task["proxy_url"])
        st.session_state.result = res
        st.session_state.task["status"] = "done"
    except VideoUnavailable:
        st.session_state.error = "Vidéo indisponible. Vérifiez l'URL et les restrictions."
        st.session_state.task["status"] = "failed"
    except HTTPError as e:
        st.session_state.error = f"HTTPError {getattr(e, 'code', '?')}: {getattr(e, 'reason', '')}"
        st.session_state.task["status"] = "failed"
    except subprocess.CalledProcessError as e:
        st.session_state.error = f"Erreur FFmpeg: {e}"
        st.session_state.task["status"] = "failed"
    except Exception as e:
        st.session_state.error = f"Erreur: {e}"
        st.session_state.task["status"] = "failed"
    finally:
        st.rerun()

# Affichage du résultat ou de l'erreur, sans relancer le job
if st.session_state.task and st.session_state.task.get("status") == "done" and st.session_state.result:
    st.success("Préparation terminée.")
    st.download_button(
        label="Télécharger le fichier zip",
        data=st.session_state.result["zip_bytes"],
        file_name=st.session_state.result["zip_name"],
        mime="application/zip",
        use_container_width=True,
    )

if st.session_state.task and st.session_state.task.get("status") == "failed" and st.session_state.error:
    st.error(st.session_state.error)
    st.info("Essayez d'activer OAuth et/ou de renseigner un proxy HTTP(S), ou testez en local.")

# Bouton de remise à zéro pour relancer un autre test sans redémarrer l'app
if st.button("Réinitialiser"):
    st.session_state.task = None
    st.session_state.result = None
    st.session_state.error = None
    st.rerun()
