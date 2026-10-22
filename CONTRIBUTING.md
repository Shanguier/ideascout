# Contributing

Thanks for looking at IdeaScout. The project is a research skill
([SKILL.md](SKILL.md)) plus a small offline Python demo
([scripts/research_agent.py](scripts/research_agent.py)).

## Setup

Python 3.11 or newer. No third-party packages.

```bash
python3 scripts/research_agent.py "your topic"
python3 -m unittest discover -s tests -v
```

`.python-version` pins 3.12 for local work. `pyproject.toml` allows 3.11 and newer.
CI runs the tests on 3.11 and 3.12.

## Scope

- Keep the license MIT.
- Do not add network calls, API keys, or a model client to the reference script
  without a written reason. The demo is supposed to run from the standard library.
- Do not invent usage numbers, stars, or benchmark scores in the docs.
- If you change CLI text or demo scoring, regenerate the saved run and commit it:

```bash
python3 scripts/research_agent.py "小米 SU7 vs Model 3 对比分析" > demo-output.txt
```

The unit tests compare that file to the command's stdout.

## Pull requests

Describe the behavior you changed and how you ran the tests. A short note in the
README changelog is enough for a user-visible demo change.
