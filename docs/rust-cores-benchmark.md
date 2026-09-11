# Bench des cœurs Rust optionnels

Deux modules du kit ont une implémentation Rust optionnelle (issue
[#354](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/354)) :
`grimoire.policies.engine.PolicyEngine.evaluate` (`rust/grimoire-policies-core/`,
`GRIMOIRE_POLICIES_BACKEND`) et `grimoire.core.schema.generate_schema` /
`grimoire.core.validator.validate_config` (`rust/grimoire-schema-core/`,
`GRIMOIRE_SCHEMA_BACKEND`). Voir `CONTRIBUTING.md#coeurs-optionnels-en-rust`
pour le contexte complet.

`scripts/bench-rust-cores.py` mesure, plutôt que suppose, ce que ces cœurs
changent : temps par appel (micro), temps de bout en bout des commandes CLI
qui les appellent (macro), et part réelle de ces fonctions dans un profil
(`importtime` + `cProfile`) de `grimoire doctor .`.

## Lancer le bench

Les deux cœurs compilés doivent être installés dans le venv actif — le
script ne les construit pas lui-même :

```bash
cargo --version   # rustup toolchain install stable si absent
pip install maturin

maturin build --release --manifest-path rust/grimoire-policies-core/Cargo.toml --out dist-rust
maturin build --release --manifest-path rust/grimoire-schema-core/Cargo.toml --out dist-rust
pip install dist-rust/*.whl   # jamais `maturin develop` hors virtualenv actif

python scripts/bench-rust-cores.py
```

Sans les roues installées, le script tourne quand même et rapporte les
chiffres Python seuls, avec une note « non mesurable » sur chaque ligne
Rust — il ne simule jamais un résultat.

Options utiles : `--json-out <fichier>` pour un dump JSON complet,
`--micro-number`/`--micro-repeat` (défaut 2000 appels × 5 séries),
`--macro-runs` (défaut 10), `--skip-macro`/`--skip-profile` pour un aller
rapide sur les micro-mesures seules.

## Ce que le script fait

- **Fixture réaliste** : un vrai projet généré par `grimoire init . -y` dans
  un `HOME` isolé et jetable — pas un config à la main.
- **Micro** : `PolicyEngine.evaluate`, `generate_schema`, `validate_config`,
  chacun en Python et en Rust (bascule par variable d'environnement, aucun
  rechargement de module nécessaire), sur l'entrée réaliste et sur une
  entrée volumineuse (100 règles de politique, config à 200 clés).
- **Macro** : `grimoire doctor .`, `grimoire host sync --dry-run`,
  `grimoire standard verify .`, et la décision `PreToolUse` via le binaire
  `grimoire-hook` — Python vs Rust, médiane de 10 exécutions, comparé à
  `grimoire --version` (coût fixe de démarrage).
- **Profil** : `python -X importtime` (top 10 imports par temps cumulé) et
  `cProfile` sur `grimoire doctor .`, avec la part de temps passée dans les
  fonctions portées elles-mêmes.

Le résultat mesuré au moment de l'écriture de ce script est publié en
commentaire sur l'issue #354 ; il ne se répète pas ici pour éviter qu'il se
périme silencieusement dans la documentation.
