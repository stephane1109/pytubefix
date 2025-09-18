# python -m streamlit run app.py


import io
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave

import streamlit as st

st.set_page_config(page_title="Test FFmpeg (Streamlit Cloud)", layout="centered")
st.title("Test FFmpeg")

# ----------------- utilitaires -----------------

def which(cmd: str) -> str | None:
    return shutil.which(cmd)

def run(cmd: list[str], timeout: int = 20):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        return 127, "", str(e)
    except Exception as e:
        return 1, "", str(e)

def gen_sine_wav(path: str, seconds: float = 1.0, freq: float = 440.0, rate: int = 44100):
    # WAV PCM 16-bit stéréo, sinusoïde simple
    n = int(seconds * rate)
    amp = 0.5
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(rate)
        for i in range(n):
            sample = int(amp * 32767.0 * math.sin(2 * math.pi * freq * (i / rate)))
            frame = struct.pack("<hh", sample, sample)
            w.writeframes(frame)

def make_download_button(label: str, filepath: str, mime: str):
    with open(filepath, "rb") as f:
        st.download_button(
            label=label,
            data=f.read(),
            file_name=os.path.basename(filepath),
            mime=mime,
            use_container_width=True,
        )

# ----------------- affichage infos -----------------

col1, col2 = st.columns(2)
with col1:
    st.markdown("**Plateforme**")
    st.code(sys.platform)
    st.markdown("**Python**")
    st.code(sys.version.split()[0])
with col2:
    st.markdown("**ffmpeg dans le PATH**")
    st.code(which("ffmpeg") or "introuvable")
    st.markdown("**ffprobe dans le PATH**")
    st.code(which("ffprobe") or "introuvable")

st.markdown("**PATH**")
st.code(os.environ.get("PATH", ""))

st.markdown("---")
st.subheader("Test 1 : ffmpeg -version")
code, out, err = run(["ffmpeg", "-hide_banner", "-version"], timeout=8)
st.write(f"Code de retour : {code}")
if code == 0:
    st.success("ffmpeg détecté")
    st.code(out.splitlines()[0] if out else "")
else:
    st.error("ffmpeg non détecté ou non exécutable dans cet environnement.")
    if err:
        st.markdown("**Erreur**")
        st.code(err)

st.markdown("---")
st.subheader("Test 2 : conversions réelles (si ffmpeg disponible)")

if code != 0:
    st.info("Les conversions ne sont pas exécutées car ffmpeg n'est pas disponible.")
else:
    if st.button("Lancer les tests de conversion"):
        logs = []
        with tempfile.TemporaryDirectory() as td:
            wav_in = os.path.join(td, "test_in.wav")
            gen_sine_wav(wav_in, seconds=1.0, freq=440.0, rate=44100)
            logs.append(f"Généré : {wav_in}")

            # 2A) WAV -> MP3 (libmp3lame), puis fallback en AAC/M4A
            mp3_out = os.path.join(td, "test_out.mp3")
            code1, out1, err1 = run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                                     "-i", wav_in, "-vn", "-c:a", "libmp3lame", "-q:a", "2", mp3_out])
            if code1 == 0 and os.path.exists(mp3_out):
                st.success("Audio: conversion WAV → MP3 réussie (libmp3lame).")
                make_download_button("Télécharger test_out.mp3", mp3_out, "audio/mpeg")
            else:
                logs.append("MP3 échoué ou indisponible, tentative AAC/M4A...")
                m4a_out = os.path.join(td, "test_out.m4a")
                code1b, out1b, err1b = run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                                            "-i", wav_in, "-vn", "-c:a", "aac", "-b:a", "128k", m4a_out])
                if code1b == 0 and os.path.exists(m4a_out):
                    st.success("Audio: conversion WAV → M4A/AAC réussie.")
                    make_download_button("Télécharger test_out.m4a", m4a_out, "audio/mp4")
                else:
                    st.error("Audio: échec des conversions MP3 et AAC.")
                    st.code((err1 or "") + "\n" + (err1b or ""))

            # 2B) Génération d'une vidéo 1s MP4 (image unie + silence)
            mp4_out = os.path.join(td, "test_out.mp4")
            # tentative H.264 + AAC via lavfi (color + anullsrc)
            cmd_h264 = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "color=c=red:s=320x240:d=1",
                        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                        "-shortest",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "128k",
                        mp4_out]
            c2, o2, e2 = run(cmd_h264, timeout=20)
            if c2 == 0 and os.path.exists(mp4_out):
                st.success("Vidéo: génération MP4 (H.264 + AAC) réussie.")
                make_download_button("Télécharger test_out.mp4", mp4_out, "video/mp4")
            else:
                # fallback: mpeg4 + aac (toujours en MP4)
                cmd_mpeg4 = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                             "-f", "lavfi", "-i", "color=c=red:s=320x240:d=1",
                             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                             "-shortest",
                             "-c:v", "mpeg4",
                             "-c:a", "aac", "-b:a", "128k",
                             mp4_out]
                c3, o3, e3 = run(cmd_mpeg4, timeout=20)
                if c3 == 0 and os.path.exists(mp4_out):
                    st.warning("Vidéo: H.264 indisponible, génération MP4 en mpeg4 + AAC réussie.")
                    make_download_button("Télécharger test_out.mp4", mp4_out, "video/mp4")
                else:
                    st.error("Vidéo: échec des deux tentatives (H.264 et mpeg4).")
                    st.code((e2 or "") + "\n" + (e3 or ""))

        st.markdown("---")
        st.markdown("Journal")
        if logs:
            st.code("\n".join(logs))
