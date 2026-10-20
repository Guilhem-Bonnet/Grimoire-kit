import pathlib

HERE = pathlib.Path(__file__).parent
TPL = (HERE / "shell.tpl.html").read_text()

GEIST = '<link rel="stylesheet" href="../../fonts/fonts.css">'
# IBM Plex et Source Sans ne sont pas vendorisées : pas de lien, repli sur la pile système.
PLEX = ''
SOURCE = ''

SKINS = {
    # Direction retenue, identité actuelle resserrée (tokens réels de forge-tokens.css)
    "Concevoir": dict(BG="#08090B", ELEV1="#15181D", ELEV2="#22272E", ELEV3="#2C323A", INK="#F6F7F8", INK2="#A9AEB6", INK3="#80868F", LINE="rgba(255,255,255,0.11)", ACC="#FF6B3D", PRI="#FF6B3D", ACCSOFT="rgba(255,107,61,0.14)", ONACC="#0B0C0E", OK="#34D399", WARN="#FCD34D", BAR="#1C2027", TERM="#0B0C0E", TERMINK="#C9CED6", RADIUS="3px", FONT="'Geist', system-ui, sans-serif", MONO="'Geist Mono', ui-monospace, Menlo, monospace", FONTLINK=GEIST, SKIN="Forge · sombre"),
    "ConcevoirClair": dict(BG="#E4E3DE", ELEV1="#F3F2EE", ELEV2="#FFFFFF", ELEV3="#DEDDD6", INK="#15171A", INK2="#4B5058", INK3="#7A8088", LINE="rgba(21,23,26,0.13)", ACC="#D9481A", PRI="#D9481A", ACCSOFT="rgba(217,72,26,0.12)", ONACC="#FFFFFF", OK="#1E8A54", WARN="#A86F0B", BAR="#EAE9E4", TERM="#1A1D22", TERMINK="#D6DAE0", RADIUS="3px", FONT="'Geist', system-ui, sans-serif", MONO="'Geist Mono', ui-monospace, Menlo, monospace", FONTLINK=GEIST, SKIN="Forge · clair"),
    # Propositions de direction artistique
    "DAArdoise": dict(BG="#0F1317", ELEV1="#1B2027", ELEV2="#2A323B", ELEV3="#353E48", INK="#EEF2F5", INK2="#A6B0BA", INK3="#7B8793", LINE="rgba(238,242,245,0.10)", ACC="#2FB8A9", PRI="#2FB8A9", ACCSOFT="rgba(47,184,169,0.16)", ONACC="#0F1418", OK="#4FCB8E", WARN="#E3B84A", BAR="#222932", TERM="#0F1317", TERMINK="#C6CFD7", RADIUS="4px", FONT="'IBM Plex Sans', system-ui, sans-serif", MONO="'IBM Plex Mono', ui-monospace, Menlo, monospace", FONTLINK=PLEX, SKIN="Ardoise · graphite et sarcelle"),
    "DAPapier": dict(BG="#E7E3D9", ELEV1="#F6F4EE", ELEV2="#FFFFFF", ELEV3="#E1DDD3", INK="#1C1B17", INK2="#5A574F", INK3="#86827A", LINE="rgba(28,27,23,0.12)", ACC="#1F6F5C", PRI="#1F6F5C", ACCSOFT="rgba(31,111,92,0.12)", ONACC="#FFFFFF", OK="#2E7D4F", WARN="#9A6B12", BAR="#EDEAE2", TERM="#1C1B17", TERMINK="#D9D5CB", RADIUS="2px", FONT="'Source Sans 3', system-ui, sans-serif", MONO="'Source Code Pro', ui-monospace, Menlo, monospace", FONTLINK=SOURCE, SKIN="Papier · clair, encre et vert profond"),
    "DAStudio": dict(BG="#232323", ELEV1="#303030", ELEV2="#404040", ELEV3="#4B4B4B", INK="#EDEDED", INK2="#B5B5B5", INK3="#8C8C8C", LINE="rgba(0,0,0,0.35)", ACC="#4F8DE6", PRI="#4F8DE6", ACCSOFT="rgba(79,141,230,0.18)", ONACC="#FFFFFF", OK="#7BC67E", WARN="#E0B85A", BAR="#3A3A3A", TERM="#1A1A1A", TERMINK="#C8C8C8", RADIUS="2px", FONT="'Geist', system-ui, sans-serif", MONO="'Geist Mono', ui-monospace, Menlo, monospace", FONTLINK=GEIST, SKIN="Studio · gris moyen, bleu, angles serrés"),
    "Encre": dict(BG="#0A0C0F", ELEV1="#161A1F", ELEV2="#232930", ELEV3="#2D343C", INK="#F2F3F5", INK2="#A8AEB7", INK3="#7E858F", LINE="rgba(255,255,255,0.11)", ACC="#F2F3F5", PRI="#FF6B3D", ACCSOFT="rgba(255,255,255,0.08)", ONACC="#0E1013", OK="#3DBE7A", WARN="#E2B33C", BAR="#1C2127", TERM="#0E1013", TERMINK="#C9CED6", RADIUS="3px", FONT="'Geist', system-ui, sans-serif", MONO="'Geist Mono', ui-monospace, Menlo, monospace", FONTLINK=GEIST, SKIN="Encre · sombre"),
    "EncreClair": dict(BG="#E5E6E2", ELEV1="#F3F3F0", ELEV2="#FFFFFF", ELEV3="#DFE0DB", INK="#17191C", INK2="#4E545C", INK3="#767C85", LINE="rgba(23,25,28,0.13)", ACC="#17191C", PRI="#D9481A", ACCSOFT="rgba(23,25,28,0.07)", ONACC="#FFFFFF", OK="#1F8A55", WARN="#9C6D0C", BAR="#EAEAE6", TERM="#1B1F25", TERMINK="#D6DAE0", RADIUS="3px", FONT="'Geist', system-ui, sans-serif", MONO="'Geist Mono', ui-monospace, Menlo, monospace", FONTLINK=GEIST, SKIN="Encre · clair"),
}

for name, vals in SKINS.items():
    out = TPL
    for key, val in vals.items():
        out = out.replace(f"%%{key}%%", val)
    assert "%%" not in out, name
    (HERE / f"{name}.dc.html").write_text(out)
    print("wrote", name)
