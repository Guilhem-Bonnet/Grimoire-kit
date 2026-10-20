import pathlib

HERE = pathlib.Path(__file__).parent


def lum(hexcolor):
    h = hexcolor.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    def ch(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# Chaque candidat : thèse, puis palettes sombre et claire avec rôles.
CANDIDATS = [
    {
        "nom": "Forge",
        "these": "L'identité actuelle resserrée : noir profond, orange vif comme accent unique.",
        "reserve": "L'orange à haute chroma vibre sur le noir et voisine avec l'ambre des avertissements : l'accent se confond avec un état.",
        "sombre": dict(bg="#0B0C0E", e1="#121418", e2="#1A1D22", e3="#22262C", ink="#F6F7F8", ink2="#A9AEB6", ink3="#80868F", acc="#FF6B3D", pri="#FF6B3D", onpri="#0B0C0E", ok="#34D399", warn="#FCD34D", bad="#F87171", s1="#1F9BBF", s2="#8A6EE0", s3="#B8880E"),
        "clair": dict(bg="#F5F4F1", e1="#FCFBF9", e2="#FFFFFF", e3="#F0EEEA", ink="#15171A", ink2="#4B5058", ink3="#7A8088", acc="#D9481A", pri="#D9481A", onpri="#FFFFFF", ok="#1E8A54", warn="#A86F0B", bad="#B93A3A", s1="#1F7FA0", s2="#6B4FC7", s3="#9A6F0A"),
    },
    {
        "nom": "Encre",
        "these": "L'accent d'interaction est l'encre elle-même ; l'orange ne reste que sur la marque et l'action primaire. Toute la chroma va aux états et aux données.",
        "reserve": "Moins de signature à l'écran : la sélection se lit par le contraste, pas par la couleur. À valider sur la sélection de nœuds.",
        "sombre": dict(bg="#0E1013", e1="#14171B", e2="#1B1F25", e3="#23282F", ink="#F2F3F5", ink2="#A8AEB7", ink3="#7E858F", acc="#F2F3F5", pri="#FF6B3D", onpri="#0E1013", ok="#3DBE7A", warn="#E2B33C", bad="#E5645A", s1="#4C9BE8", s2="#B07CE8", s3="#D99A2B"),
        "clair": dict(bg="#F6F6F4", e1="#FBFBFA", e2="#FFFFFF", e3="#EFEFEC", ink="#17191C", ink2="#4E545C", ink3="#767C85", acc="#17191C", pri="#D9481A", onpri="#FFFFFF", ok="#1F8A55", warn="#9C6D0C", bad="#B83A36", s1="#1F6FBF", s2="#6B48C4", s3="#9A6A08"),
    },
    {
        "nom": "Ardoise",
        "these": "Graphite froid, accent sarcelle : la couleur d'interaction est loin des trois états.",
        "reserve": "Le sarcelle et le vert « ok » se rapprochent pour un daltonien deutéranope ; l'écart mesuré reste suffisant mais sans marge.",
        "sombre": dict(bg="#14181D", e1="#1B2027", e2="#232A32", e3="#2C343D", ink="#EEF2F5", ink2="#A6B0BA", ink3="#7B8793", acc="#2FB8A9", pri="#2FB8A9", onpri="#0F1418", ok="#4FCB8E", warn="#E3B84A", bad="#EA7069", s1="#4C9BE8", s2="#B07CE8", s3="#D99A2B"),
        "clair": dict(bg="#F2F4F5", e1="#F9FAFB", e2="#FFFFFF", e3="#EBEEF0", ink="#171B1F", ink2="#4C555E", ink3="#737D87", acc="#12796F", pri="#12796F", onpri="#FFFFFF", ok="#1F8A55", warn="#9C6D0C", bad="#B83A36", s1="#1F6FBF", s2="#6B48C4", s3="#9A6A08"),
    },
    {
        "nom": "Studio",
        "these": "Le gris moyen des éditeurs 3D et un accent bleu : la convention la plus répandue des outils professionnels.",
        "reserve": "Le gris moyen réduit la plage de contraste disponible ; les surfaces se distinguent par des ombres noires, pas par des bordures.",
        "sombre": dict(bg="#2B2B2B", e1="#303030", e2="#383838", e3="#424242", ink="#EDEDED", ink2="#B5B5B5", ink3="#8C8C8C", acc="#4F8DE6", pri="#4F8DE6", onpri="#FFFFFF", ok="#7BC67E", warn="#E0B85A", bad="#E8736C", s1="#5DC3D8", s2="#B07CE8", s3="#D99A2B"),
        "clair": dict(bg="#E9E9E9", e1="#F2F2F2", e2="#FFFFFF", e3="#DEDEDE", ink="#1B1B1B", ink2="#4F4F4F", ink3="#767676", acc="#2469C2", pri="#2469C2", onpri="#FFFFFF", ok="#1F8A55", warn="#9C6D0C", bad="#B83A36", s1="#177E96", s2="#6B48C4", s3="#9A6A08"),
    },
    {
        "nom": "Papier",
        "these": "Clair d'abord : blanc cassé chaud, encre, vert profond. Le sombre en dérive.",
        "reserve": "Le vert profond comme accent et le vert « ok » comme état demandent deux verts bien séparés ; ici l'état est plus clair et plus jaune.",
        "sombre": dict(bg="#151412", e1="#1B1A17", e2="#23211D", e3="#2B2925", ink="#F1EEE7", ink2="#B3AFA5", ink3="#85817A", acc="#5FBFA2", pri="#5FBFA2", onpri="#151412", ok="#8FD36A", warn="#E3B84A", bad="#EA7069", s1="#4C9BE8", s2="#B07CE8", s3="#D99A2B"),
        "clair": dict(bg="#F3F0E8", e1="#FAF8F3", e2="#FFFFFF", e3="#ECE8DF", ink="#1C1B17", ink2="#5A574F", ink3="#86827A", acc="#1F6F5C", pri="#1F6F5C", onpri="#FFFFFF", ok="#4E8A2A", warn="#9A6B12", bad="#B83A36", s1="#1F6FBF", s2="#6B48C4", s3="#9A6A08"),
    },
]


def sw(hexc, label, on=None, small=False):
    h = "34px" if small else "44px"
    txt = ""
    if on:
        c = contrast(hexc, on)
        cls = "ok" if c >= 4.5 else ("mid" if c >= 3 else "bad")
        txt = f'<span class="cr {cls}">{c:.1f}</span>'
    return (f'<div class="sw" style="height: {h};"><div class="sq" style="background: {hexc};"></div>'
            f'<div class="swt"><span>{label}</span><span class="mono">{hexc}</span></div>{txt}</div>')


def colonne(c):
    d, l = c["sombre"], c["clair"]
    out = [f'<div class="col"><div class="nom">{c["nom"]}</div><div class="these">{c["these"]}</div>']
    out.append('<div class="lbl">Sombre · contraste sur la surface e1</div>')
    out.append('<div class="stack" style="background: %s;">' % d["e1"])
    out.append(sw(d["bg"], "fond"))
    out.append(sw(d["e2"], "surface 2"))
    out.append(sw(d["ink"], "encre", d["e1"]))
    out.append(sw(d["ink2"], "encre douce", d["e1"]))
    out.append(sw(d["ink3"], "encre discrète", d["e1"]))
    out.append(sw(d["acc"], "accent", d["e1"]))
    out.append(sw(d["pri"], "action primaire", d["e1"]) if d["pri"] != d["acc"] else "")
    out.append(sw(d["ok"], "ok", d["e1"], True))
    out.append(sw(d["warn"], "à vérifier", d["e1"], True))
    out.append(sw(d["bad"], "bloqué", d["e1"], True))
    out.append('<div class="series">' + "".join(f'<div style="background: {d[k]};"></div>' for k in ("s1", "s2", "s3")) + '<span class="lbl">séries</span></div>')
    out.append('</div>')
    out.append('<div class="lbl">Clair · contraste sur la surface e1</div>')
    out.append('<div class="stack" style="background: %s;">' % l["e1"])
    out.append(sw(l["bg"], "fond"))
    out.append(sw(l["ink"], "encre", l["e1"]))
    out.append(sw(l["ink2"], "encre douce", l["e1"]))
    out.append(sw(l["ink3"], "encre discrète", l["e1"]))
    out.append(sw(l["acc"], "accent", l["e1"]))
    out.append(sw(l["pri"], "action primaire", l["e1"]) if l["pri"] != l["acc"] else "")
    out.append(sw(l["ok"], "ok", l["e1"], True))
    out.append(sw(l["warn"], "à vérifier", l["e1"], True))
    out.append(sw(l["bad"], "bloqué", l["e1"], True))
    out.append('<div class="series">' + "".join(f'<div style="background: {l[k]};"></div>' for k in ("s1", "s2", "s3")) + '<span class="lbl">séries</span></div>')
    out.append('</div>')
    # échantillon d'interface, sombre
    out.append(f'''<div class="lbl">Échantillon sombre</div>
<div class="sample" style="background: {d["bg"]}; color: {d["ink"]};">
  <div class="sbar" style="background: {d["e1"]}; border-bottom: 1px solid rgba(255,255,255,.1);"><span style="border-bottom: 2px solid {d["acc"]}; padding-bottom: 6px; color: {d["ink"]};">Concevoir</span><span style="color: {d["ink2"]};">Observer</span><span style="flex-grow: 1;"></span><span class="sbtn" style="background: {d["pri"]}; color: {d["onpri"]};">Compiler</span></div>
  <div class="srow" style="background: {d["e1"]};"><span style="color: {d["ink2"]};">party-mode</span><span class="sdot" style="background: {d["ok"]};"></span></div>
  <div class="srow" style="background: {d["e3"]}; color: {d["ink"]}; box-shadow: inset 2px 0 0 {d["acc"]};"><span>boomerang-orchestration</span><span class="sdot" style="background: {d["warn"]};"></span></div>
  <div class="srow" style="background: {d["e1"]};"><span style="color: {d["ink2"]};">incident-response</span><span class="sdot" style="background: {d["bad"]};"></span></div>
</div>''')
    out.append(f'<div class="reserve">{c["reserve"]}</div></div>')
    return "".join(out)


html = f'''<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <link rel="stylesheet" href="../../fonts/fonts.css">
  <style>
    body {{ margin: 0; font-family: 'Geist', system-ui, sans-serif; color: #1C1E22; background: #F4F4F2; }}
    a {{ color: #B4432B; }} a:hover {{ color: #1C1E22; }}
    .mono {{ font-family: 'Geist Mono', ui-monospace, Menlo, monospace; font-size: 11px; color: #6B7079; }}
    .lbl {{ font-size: 11px; color: #6B7079; margin: 10px 0 4px; }}
    .col {{ background: #FFFFFF; border: 1px solid #D9D9D4; border-radius: 3px; padding: 14px; display: flex; flex-direction: column; min-width: 0; }}
    .nom {{ font-size: 17px; font-weight: 600; }}
    .these {{ font-size: 12.5px; color: #4B5058; line-height: 1.4; margin-top: 4px; min-height: 52px; }}
    .stack {{ border-radius: 3px; padding: 8px; display: flex; flex-direction: column; gap: 4px; }}
    .sw {{ display: flex; align-items: center; gap: 8px; }}
    .sq {{ width: 34px; align-self: stretch; border-radius: 2px; border: 1px solid rgba(0,0,0,.15); flex: none; }}
    .swt {{ display: flex; flex-direction: column; font-size: 12px; color: #FFFFFF; mix-blend-mode: difference; flex-grow: 1; }}
    .swt .mono {{ color: inherit; }}
    .cr {{ font-family: 'Geist Mono', ui-monospace, Menlo, monospace; font-size: 11px; padding: 1px 5px; border-radius: 2px; flex: none; }}
    .cr.ok {{ background: #DDF3E6; color: #1E6B42; }} .cr.mid {{ background: #F8ECCB; color: #7A5308; }} .cr.bad {{ background: #F7DADA; color: #8E2B2B; }}
    .series {{ display: flex; align-items: center; gap: 6px; margin-top: 4px; }}
    .series > div {{ width: 26px; height: 14px; border-radius: 2px; }}
    .series .lbl {{ margin: 0 0 0 4px; color: #B5B9C0; }}
    .sample {{ border-radius: 3px; overflow: hidden; font-size: 12px; }}
    .sbar {{ display: flex; align-items: center; gap: 14px; padding: 10px 12px 4px; }}
    .sbtn {{ padding: 4px 9px; border-radius: 3px; font-weight: 500; margin-bottom: 4px; }}
    .srow {{ display: flex; align-items: center; justify-content: space-between; padding: 7px 12px; }}
    .sdot {{ width: 7px; height: 7px; border-radius: 999px; }}
    .reserve {{ font-size: 12px; color: #4B5058; line-height: 1.4; margin-top: 12px; padding-top: 10px; border-top: 1px solid #E5E5E0; }}
    .legend {{ display: flex; gap: 14px; font-size: 12px; color: #4B5058; align-items: center; }}
  </style>
</helmet>
<div style="width: 1900px; height: 1180px; padding: 28px 32px; box-sizing: border-box; display: flex; flex-direction: column; gap: 16px; background: #F4F4F2; overflow: hidden;">
  <div style="display: flex; align-items: flex-end; justify-content: space-between;">
    <div><div style="font-size: 20px; font-weight: 600;">Palettes candidates, mesurées</div><div style="font-size: 13px; color: #4B5058; margin-top: 4px; max-width: 900px;">Mêmes rôles dans chaque colonne : trois surfaces, trois encres, un accent d'interaction, une action primaire quand elle diffère, trois états, trois séries de graphiques. Le chiffre est le contraste WCAG sur la surface des panneaux.</div></div>
    <div class="legend"><span class="cr ok">≥ 4,5</span> texte AA <span class="cr mid">3 à 4,5</span> grands textes et icônes <span class="cr bad">&lt; 3</span> décoratif seulement</div>
  </div>
  <div style="display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 14px; flex-grow: 1; min-height: 0;">
    {"".join(colonne(c) for c in CANDIDATS)}
  </div>
</div>
</x-dc>
</body>
</html>
'''
(HERE / "Couleurs.dc.html").write_text(html)
print("wrote Couleurs.dc.html")
for c in CANDIDATS:
    d = c["sombre"]
    print(c["nom"], "sombre  encre2", round(contrast(d["ink2"], d["e1"]), 1), "encre3", round(contrast(d["ink3"], d["e1"]), 1), "accent", round(contrast(d["acc"], d["e1"]), 1), "warn", round(contrast(d["warn"], d["e1"]), 1), "acc/warn", round(contrast(d["acc"], d["warn"]), 2))
