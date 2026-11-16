# Contributing to HumanWrite

Thanks for helping! HumanWrite aims to be simple for writers and trustworthy with their documents. Please keep both in mind.

## Ground rules

1. **Never damage a user's document.** The original `.docx` is read-only. Edits go through `backend/app/services/ooxml/paragraph_editor.py`. If an edit can't be applied safely, refuse it with a clear reason; don't guess.
2. **Never fake anything.** Progress, analysis results and AI output must come from real processing. AI text must come from a real model stream.
3. **Local first.** Don't add cloud services or telemetry. Any optional network feature must be off by default and explained in plain language in the UI.
4. **Be honest about uncertainty.** Similarity is not proof of plagiarism. Writing-pattern signals are not authorship detection. Suggestions that add facts or sources must be marked for verification.
5. **Keep the default UI simple.** New power features belong in the Advanced editor or the Full report, not on the main review screen.

## Development setup

```bash
./start.sh                       # installs everything on first run
cd backend && .venv/bin/python -m pytest -q
cd frontend && npx tsc --noEmit && npx eslint .
```

You don't need a model to run the tests: AI code paths run against `backend/tests/fake_ollama.py`.

## Pull requests

- Add or update tests. For anything touching DOCX editing or export, add a case to `backend/tests/test_ooxml_preservation.py`.
- Run the backend tests, typecheck and lint before opening the PR.
- For UI changes, include a screenshot and check:
  - the change works with keyboard only;
  - it respects "reduce motion".
- Keep copy in plain, friendly English. Say "Accept", not "Apply transformation".

## Reporting bugs

If a document exported incorrectly, the most useful report includes:
- a minimal `.docx` that shows the problem (remove anything private);
- what you changed;
- what looked wrong in Word.

Please don't attach personal documents.
