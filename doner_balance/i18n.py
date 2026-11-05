"""Localization: translation keys for every player-facing name, a Godot CSV, missing-text checks.

Every named thing in the economy (game, currency, tiers, staff, upgrades, managers, tap upgrades,
NPC events and their answers) gets a stable key such as ``STAFF_NEPHEW_NAME``. The text in
``config/economy.yaml`` is the default language. Other languages live in
``<config dir>/<localization.translations_dir>/<lang>.yaml`` as ``KEY: text`` mappings.

``write_translations_csv`` writes Godot's CSV translation format (first column ``keys``, one
column per locale); drop it into the Godot project and it is imported as translations.
"""

from __future__ import annotations

import csv
from pathlib import Path

import yaml

from .config import EconomyConfig


def make_key(*parts: str) -> str:
    return "_".join(p.upper().replace("-", "_") for p in parts)


def name_keys(cfg: EconomyConfig) -> dict[tuple[str, str], str]:
    """(kind, id) -> translation key for every named object."""
    keys: dict[tuple[str, str], str] = {
        ("game", ""): "GAME_NAME",
        ("currency", ""): "CURRENCY_NAME",
        ("prestige", ""): "PRESTIGE_CURRENCY_NAME",
    }
    for u in cfg.tap.upgrades:
        keys[("tap", u.id)] = make_key("TAP_UPGRADE", u.id, "NAME")
    for pk in cfg.prestige.perks:
        keys[("perk", pk.id)] = make_key("PERK", pk.id, "NAME")
    for t in cfg.tiers:
        keys[("tier", t.id)] = make_key("TIER", t.id, "NAME")
        for s in t.staff:
            keys[("staff", s.id)] = make_key("STAFF", s.id, "NAME")
        for up in t.upgrades:
            keys[("upgrade", up.id)] = make_key("UPGRADE", up.id, "NAME")
        for m in t.managers:
            keys[("manager", m.id)] = make_key("MANAGER", m.id, "NAME")
    for e in cfg.events:
        keys[("event", e.id)] = make_key("EVENT", e.id, "NAME")
        for r in e.responses:
            keys[("response", f"{e.id}.{r.id}")] = make_key("EVENT", e.id, "RESPONSE", r.id, "NAME")
    seen: dict[str, tuple[str, str]] = {}
    for obj, k in keys.items():
        if k in seen:
            raise ValueError(
                f"translation key collision: {seen[k]} and {obj} both map to {k} "
                "(ids that differ only by '-' vs '_' or letter case)"
            )
        seen[k] = obj
    return keys


def source_texts(cfg: EconomyConfig) -> dict[str, str]:
    """Translation key -> text in the default language (the names in the YAML), in a stable order."""
    k = name_keys(cfg)
    out = {
        k[("game", "")]: cfg.name,
        k[("currency", "")]: cfg.currency.name,
        k[("prestige", "")]: cfg.prestige.currency_name,
    }
    for u in cfg.tap.upgrades:
        out[k[("tap", u.id)]] = u.name
    for pk in cfg.prestige.perks:
        out[k[("perk", pk.id)]] = pk.name
    for t in cfg.tiers:
        out[k[("tier", t.id)]] = t.name
        for s in t.staff:
            out[k[("staff", s.id)]] = s.name
        for up in t.upgrades:
            out[k[("upgrade", up.id)]] = up.name
        for m in t.managers:
            out[k[("manager", m.id)]] = m.name
    for e in cfg.events:
        out[k[("event", e.id)]] = e.name
        for r in e.responses:
            out[k[("response", f"{e.id}.{r.id}")]] = r.name
    return out


def load_translations(cfg: EconomyConfig, directory: Path | None) -> dict[str, dict[str, str]]:
    """Read ``<lang>.yaml`` for every non-default language (missing file = nothing translated yet)."""
    out: dict[str, dict[str, str]] = {}
    for lang in cfg.localization.languages:
        if lang == cfg.localization.default_language:
            continue
        data: dict[str, str] = {}
        if directory is not None:
            f = directory / f"{lang}.yaml"
            if f.is_file():
                raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                if not isinstance(raw, dict):
                    raise ValueError(f"{f} must be a mapping of KEY: text")
                data = {str(k): str(v) for k, v in raw.items() if v is not None and str(v).strip()}
        out[lang] = data
    return out


def missing_translations(cfg: EconomyConfig, translations: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    keys = list(source_texts(cfg))
    return {lang: [k for k in keys if k not in texts] for lang, texts in translations.items()}


def unknown_translation_keys(cfg: EconomyConfig, translations: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    """Keys in a translation file that no longer exist in the economy (renamed or removed items)."""
    keys = set(source_texts(cfg))
    return {lang: sorted(set(texts) - keys) for lang, texts in translations.items()}


def write_translations_csv(
    cfg: EconomyConfig, path: str | Path, translations: dict[str, dict[str, str]] | None = None
) -> Path:
    """Godot CSV translation file. Untranslated cells stay empty (see docs/GODOT_EXPORT.md)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    translations = translations or {}
    default = cfg.localization.default_language
    langs = [default, *[lang for lang in cfg.localization.languages if lang != default]]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["keys", *langs])
        for key, text in source_texts(cfg).items():
            w.writerow([key, text, *[translations.get(lang, {}).get(key, "") for lang in langs[1:]]])
    return path
