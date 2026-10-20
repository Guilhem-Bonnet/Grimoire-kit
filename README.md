<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/wordmark-dark.svg">
    <img src="docs/assets/brand/wordmark-light.svg" width="220" alt="Grimoire">
  </picture>
</p>

# grimoire-kit

[![PyPI](https://img.shields.io/pypi/v/grimoire-kit?style=flat&label=PyPI)](https://pypi.org/project/grimoire-kit/)
[![CI](https://img.shields.io/github/actions/workflow/status/Guilhem-Bonnet/Grimoire-kit/ci-sdk.yml?branch=main&style=flat&label=CI)](https://github.com/Guilhem-Bonnet/Grimoire-kit/actions/workflows/ci-sdk.yml)
[![License](https://img.shields.io/github/license/Guilhem-Bonnet/Grimoire-kit?style=flat&label=license)](LICENSE)

Grimoire installs into your repository and gives you, for the code you delegate to agents
(Claude Code, Copilot), a local dashboard, tasks, project memory and an evidence check.

[Website](https://guilhem-bonnet.github.io/Grimoire-kit/) ·
[Manual (in French)](https://guilhem-bonnet.github.io/Grimoire-kit/docs/) ·
[Version française](README.fr.md)

```bash
uv tool install grimoire-kit                   # or: pipx install grimoire-kit
grimoire init . --lite -y                      # sets up the current repository, light profile (no service)
grimoire standard init . --profile governed    # makes the evidence check blocking
```

## The five tools

### The dashboard

A local web page that gathers the Grimoire projects on your machine and their tasks. Started
from a project, it adds that project and opens on it; it does not start sessions, that stays
in your editor.

```bash
grimoire cockpit start    # starts in the background and opens the browser
```

### Tasks

Each piece of work becomes a task with an acceptance criterion. A session takes a task and
hands its work back to you for review.

```bash
grimoire task add "Fix the CSV export" -a "pytest tests/test_export.py passes" --ready
```

### Project memory

What the project decided carries over from one session to the next. Memory belongs to each
project; in the light profile it needs no service.

```bash
grimoire memory remember "We keep SQLite while the volume allows it"
```

### Evidence check

Before a task changes state, Grimoire checks that its evidence exists: a test run, a written
review, a produced file. It refuses for real in the `governed` profile and warns elsewhere;
either way, it names what is missing.

```bash
grimoire standard gate check    # tells which evidence is missing, if any
```

### Install

Start small and enable only what the project needs. Updating goes through a backup and a
preview; anything that needs your call is proposed instead of applied.

```bash
grimoire upgrade-flow run    # guided update, backup first
```

## Under the hood

The manual is in French.

- [Getting started](https://guilhem-bonnet.github.io/Grimoire-kit/docs/getting-started/) ·
  [Command reference](https://guilhem-bonnet.github.io/Grimoire-kit/docs/cli-reference/)
- [Project memory](https://guilhem-bonnet.github.io/Grimoire-kit/docs/memory-system/) ·
  [Evidence check](https://guilhem-bonnet.github.io/Grimoire-kit/docs/standard/) ·
  [Updating](https://guilhem-bonnet.github.io/Grimoire-kit/docs/upgrade/)
- [Hosts, surface by surface](https://guilhem-bonnet.github.io/Grimoire-kit/docs/hosts/)
- [Repository architecture](ARCHITECTURE.md) · [Changelog](CHANGELOG.md)

## Contributing

Issues and change proposals are welcome. The path (environment, tests, contribution rules) is
in [CONTRIBUTING.md](CONTRIBUTING.md). A change to a command updates its reference page in the
same commit.

## License

MIT — see [LICENSE](LICENSE).
