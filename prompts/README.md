# Prompts

One folder per agent, one file per version: `prompts/classifier/v1.md`.

Never edit a released version. Copy it to `v2.md` and change that, the same rule as
`config/tolerances/`. Every agent answer records the prompt version and a fingerprint (a hash of
the text), so an edit made without a new version still shows up.
