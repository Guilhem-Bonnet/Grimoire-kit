"""Prépare les rushes du teaser : extrait des séquences JPEG à 30 i/s (1920×1080, recadrage « cover »).

Usage : python3 -I prep_footage.py [<dossier teaser> [<dossier des rushes>]] [--placeholders]
Par défaut : le dossier du script, et <dossier teaser>/footage.
Lit <v4 dir>/clips.json : {"id": {"file": "jeu/xxx.mp4", "in": 2.0, "dur": 4.0, "crop": "cover"|"contain"}}
Écrit <v4 dir>/frames/<id>/0001.jpg … et <v4 dir>/clips.manifest.json (id → n, fps, w, h).
Avec --placeholders, fabrique des séquences grises (dégradé + nom du plan) pour les ids sans fichier.
"""
import json
import pathlib
import subprocess
import sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
v4 = pathlib.Path(args[0]) if args else pathlib.Path(__file__).resolve().parent
foot = pathlib.Path(args[1]) if len(args) > 1 else v4 / "footage"
placeholders = "--placeholders" in sys.argv
clips = json.loads((v4 / "clips.json").read_text())
manifest = {}
FPS = 30
for cid, c in clips.items():
    out = v4 / "frames" / cid
    out.mkdir(parents=True, exist_ok=True)
    existing = sorted(out.glob("*.jpg"))
    src = foot / c["file"] if c.get("file") else None
    dur = float(c.get("dur", 3.0))
    n = round(dur * FPS)
    if src and src.exists():
        if len(existing) == n and not c.get("force"):
            manifest[cid] = {"n": n, "fps": FPS}
            continue
        for old in existing:
            old.unlink()
        w, h = c.get("w", 1920), c.get("h", 1080)
        mode = c.get("crop", "cover")
        if mode == "cover":
            vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
        else:
            vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
        if c.get("flip"):
            vf += ",hflip"
        if c.get("speed") and c["speed"] != 1:
            vf = f"setpts={1/c['speed']}*PTS," + vf
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(c.get("in", 0)), "-i", str(src), "-t", str(dur / (c.get("speed") or 1)),
               "-vf", vf + f",fps={FPS}", "-q:v", str(c.get("q", 4)), "-frames:v", str(n), str(out / "%04d.jpg")]
        subprocess.run(cmd, check=True)
        got = len(list(out.glob("*.jpg")))
        manifest[cid] = {"n": got, "fps": FPS, "src": str(c["file"]), "in": c.get("in", 0)}
        print(cid, got, "images depuis", c["file"])
    elif placeholders:
        if len(existing) >= n:
            manifest[cid] = {"n": n, "fps": FPS, "placeholder": True}
            continue
        label = c.get("label", cid)
        for i in range(1, n + 1):
            p = out / f"{i:04d}.jpg"
            if p.exists():
                continue
            shade = 24 + (i % 30)
            subprocess.run(["magick", "-size", "1920x1080", f"gradient:#1c2230-#{shade:02x}{shade+6:02x}{shade+14:02x}",
                            "-gravity", "center", "-fill", "#8ea2ff", "-pointsize", "72", "-annotate", "0", f"[{label}]",
                            "-quality", "70", str(p)], check=True)
        manifest[cid] = {"n": n, "fps": FPS, "placeholder": True}
        print(cid, n, "images de remplacement")
    else:
        print("ABSENT", cid, c.get("file"))
(v4 / "clips.manifest.json").write_text(json.dumps(manifest, indent=1))
print("manifest", len(manifest))
