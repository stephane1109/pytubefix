# python -m streamlit run app.py


import os
import io
import shutil
import subprocess
import tempfile
import zipfile

import streamlit as st
from pytubefix import YouTube
from pytubefix.exceptions import VideoUnavailable

st.set_page_config(page_title="YouTube → MP4+MP3+WAV", layout="centered")

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

st.title("YouTube → MP4 (HD) + MP3 + WAV")

url = st.text_input("URL YouTube", placeholder="https://www.youtube.com/watch?v=XXXXXXXXXXX")

# 1) Afficher la vidéo en premier
if url.strip():
    st.video(url.strip())

# 2) Un seul bouton qui fait tout
if st.button("Télécharger (MP4+MP3+WAV)"):
    if not ffmpeg_available():
        st.error("ffmpeg introuvable. Installez-le et relancez.")
    elif not url.strip():
        st.error("Veuillez entrer une URL.")
    else:
        try:
            with st.spinner("Analyse de la vidéo et authentification OAuth si nécessaire…"):
                yt = YouTube(
                    url.strip(),
                    use_oauth=True,          # OAuth activé
                    allow_oauth_cache=True   # mise en cache des tokens
                )
                base = sanitize(yt.title)

            with tempfile.TemporaryDirectory() as tmp:
                with st.spinner("Téléchargement des flux vidéo et audio…"):
                    v, a = pick_streams(yt)
                    if not v or not a:
                        st.error("Impossible de trouver des flux vidéo/audio adaptatifs.")
                        st.stop()
                    v_path = v.download(output_path=tmp, filename=base + "_v")
                    a_path = a.download(output_path=tmp, filename=base + "_a")

                mp4_path = os.path.join(tmp, base + ".mp4")
                with st.spinner("Fusion vidéo+audio en MP4…"):
                    merge_to_mp4(v_path, a_path, mp4_path)

                mp3_path = os.path.join(tmp, base + ".mp3")
                wav_path = os.path.join(tmp, base + ".wav")
                with st.spinner("Génération MP3…"):
                    make_mp3(mp4_path, mp3_path)
                with st.spinner("Génération WAV…"):
                    make_wav(mp4_path, wav_path)

                with st.spinner("Préparation du fichier ZIP…"):
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
                        z.write(mp4_path, arcname=os.path.basename(mp4_path))
                        z.write(mp3_path, arcname=os.path.basename(mp3_path))
                        z.write(wav_path, arcname=os.path.basename(wav_path))
                    buf.seek(0)
                    st.session_state["zip_bytes"] = buf.getvalue()
                    st.session_state["zip_name"] = f"{base}.zip"

            st.success("Préparation terminée.")
        except VideoUnavailable:
            st.error("Vidéo indisponible. Vérifiez l’URL et les restrictions.")
        except subprocess.CalledProcessError as e:
            st.error(f"Erreur FFmpeg: {e}")
        except Exception as e:
            st.error(f"Erreur: {e}")

# 3) Bouton de téléchargement unique, indépendant des reruns
if "zip_bytes" in st.session_state and "zip_name" in st.session_state:
    st.download_button(
        label="Télécharger le fichier zip",
        data=st.session_state["zip_bytes"],
        file_name=st.session_state["zip_name"],
        mime="application/zip",
        use_container_width=True,
    )

st.caption("Téléchargez uniquement des contenus pour lesquels vous avez les droits ou une autorisation.")
