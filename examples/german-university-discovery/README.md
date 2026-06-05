# Example: German University Researcher Discovery

This directory contains a reference implementation of the nanobot framework applied to
systematic researcher discovery across German universities.

## What it demonstrates

Given a research topic (e.g. "machine learning", "computational biology", "NLP"), the
pipeline:

1. Sweeps a configurable list of German universities for relevant research groups
2. Profiles each group (PI, focus, recent papers, GitHub activity)
3. Scores groups for relevance to the user's topic
4. Optionally identifies open-source repositories for contribution

The same pipeline structure works for any country, institution type, or research domain —
adapt the institution list, relevance criteria, and schemas for your use case.

## Contents

| File | Purpose |
|------|---------|
| `PROTOCOL.md` | Full pipeline protocol — copy to `shared/PROTOCOL.md` in your overlay |
| `schemas/institution.schema.json` | Schema for institution target entries |
| `schemas/research_group.schema.json` | Schema for research group profiles |

## How to use

1. Copy `PROTOCOL.md` → `<your-project-overlay>/shared/PROTOCOL.md`
2. Edit: set your topic keywords, relevance criteria, and institution list
3. Copy schemas to `<your-project-overlay>/shared/schemas/`
4. Rename state files in PROTOCOL.md if you prefer different filenames
5. Start the dispatcher and ask it to begin Stage 1

See the root `README.md` for full framework setup instructions.
