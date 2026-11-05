# Translations

The names in `config/economy.yaml` are the default language (`localization.default_language`).
For every other language listed in `localization.languages`, put a `<lang>.yaml` file here with
one `KEY: text` line per name, for example `uk.yaml`:

```yaml
GAME_NAME: Донер Ламар
TIER_KIOSK_NAME: Кіоск біля метро
STAFF_NEPHEW_NAME: Племінник Арсен (помічник)
```

`python -m doner_balance validate` lists missing and stale keys; `python -m doner_balance export`
writes all languages to `export/translations.csv` (Godot's CSV translation format).
The full list of keys is the first column of `export/translations.csv`.
