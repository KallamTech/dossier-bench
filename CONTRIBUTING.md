# Contributing

Issues and pull requests are welcome.

## Developer Certificate of Origin

Every commit must be signed off (`git commit -s`). That adds a `Signed-off-by: Name <email>` line and
certifies the [Developer Certificate of Origin](https://developercertificate.org/): you wrote the change,
or have the right to submit it under this project's licences (Apache-2.0 for code, CC BY 4.0 for cases).

## Development

```
uv sync --group test
uv run --group test python -m pytest     # offline: no model, no network
```

## Cases

- Fictional parties, people and addresses only; email domains end in `.example`.
- Everything in `expected/` and `docs/` is generated from `bible.yaml` + `source/` by the case's
  `generate.py`, byte-deterministically. Change the bible, re-run with `--force`, bump `case_version`.
- Scenario expectations state facts in the case's own words (`must_facts`) and things a correct answer
  must not assert (`must_not_claim`). Keep them about content, never about how a particular system works.
- Results are only comparable within one `case_version` and one judge model.
