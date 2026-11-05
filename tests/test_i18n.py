import csv
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from doner_balance.config import config_from_dict
from doner_balance.export import FORMAT_VERSION, build_export, export_json
from doner_balance.i18n import (
    load_translations,
    missing_translations,
    name_keys,
    source_texts,
    unknown_translation_keys,
    write_translations_csv,
)

from .conftest import DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parents[1]


def _with_langs(langs, default="en"):
    d = yaml.safe_load(DEFAULT_CONFIG.read_text())
    d["localization"] = {"default_language": default, "languages": langs}
    return config_from_dict(d)


def test_every_named_object_has_a_unique_key(default_cfg):
    keys = name_keys(default_cfg)
    assert len(set(keys.values())) == len(keys)
    texts = source_texts(default_cfg)
    assert texts["STAFF_NEPHEW_NAME"] == "Nephew Arsen (helper)"
    assert texts["EVENT_MAFIA_RESPONSE_PAY_NAME"] == "Pay protection"
    assert all(re.fullmatch(r"[A-Z0-9_]+", k) for k in texts)


def test_export_carries_name_keys(default_cfg):
    d = build_export(default_cfg)
    assert d["format_version"] == FORMAT_VERSION >= 3
    assert d["localization"]["csv"] == "translations.csv"
    texts = source_texts(default_cfg)
    named = [d["currency"], *d["tap"]["upgrades"], *d["prestige"]["perks"]]
    for t in d["tiers"]:
        named += [t, *t["staff"], *t["upgrades"], *t["managers"]]
    for e in d["events"]:
        named += [e, *e["responses"]]
    for obj in named:
        assert texts[obj["name_key"]] == obj["name"]
    assert texts[d["prestige"]["currency_name_key"]] == d["prestige"]["currency_name"]


def test_gdscript_supports_the_current_format_version():
    gd = (ROOT / "godot" / "economy.gd").read_text()
    m = re.search(r"const SUPPORTED_VERSION := (\d+)", gd)
    assert m and int(m.group(1)) == FORMAT_VERSION
    assert "func display_name" in gd


def test_translation_files_missing_and_stale_keys(tmp_path):
    cfg = _with_langs(["en", "uk"])
    (tmp_path / "uk.yaml").write_text(
        "GAME_NAME: Донер Ламар\nTIER_KIOSK_NAME: Кіоск біля метро\nOLD_ITEM_NAME: щось\nEMPTY_ONE: ''\n",
        encoding="utf-8",
    )
    tr = load_translations(cfg, tmp_path)
    assert tr["uk"]["GAME_NAME"] == "Донер Ламар"
    assert "EMPTY_ONE" not in tr["uk"]  # blank texts do not count as translated
    missing = missing_translations(cfg, tr)["uk"]
    assert "GAME_NAME" not in missing and "STAFF_NEPHEW_NAME" in missing
    assert len(missing) == len(source_texts(cfg)) - 2
    assert unknown_translation_keys(cfg, tr)["uk"] == ["OLD_ITEM_NAME"]


def test_missing_translation_dir_means_nothing_translated(tmp_path):
    cfg = _with_langs(["en", "de"])
    tr = load_translations(cfg, tmp_path / "nope")
    assert tr == {"de": {}}


def test_translation_csv_is_godot_format(tmp_path):
    cfg = _with_langs(["en", "uk", "pt_BR"])
    path = write_translations_csv(cfg, tmp_path / "t.csv", {"uk": {"GAME_NAME": 'Донер, "Ламар"'}})
    rows = list(csv.reader(path.open(encoding="utf-8")))
    assert rows[0] == ["keys", "en", "uk", "pt_BR"]
    assert rows[1] == ["GAME_NAME", "Doner Lamar", 'Донер, "Ламар"', ""]
    assert len(rows) == 1 + len(source_texts(cfg))


def test_export_json_writes_csv_next_to_it(default_cfg, tmp_path):
    p = export_json(default_cfg, tmp_path / "out" / "economy.json")
    assert (p.parent / "translations.csv").is_file()


@pytest.mark.parametrize(
    "loc, message",
    [
        ({"default_language": "de", "languages": ["en"]}, "default_language must be one of"),
        ({"languages": ["en", "en"]}, "must be unique"),
        ({"languages": ["english"]}, "should match pattern"),
        ({"translations_dir": "../secrets"}, "should match pattern"),
    ],
)
def test_localization_validation(loc, message):
    d = yaml.safe_load(DEFAULT_CONFIG.read_text())
    d["localization"] = loc
    with pytest.raises(ValidationError, match=message):
        config_from_dict(d)


def test_key_collision_is_rejected_by_validation():
    d = yaml.safe_load(DEFAULT_CONFIG.read_text())
    d["tiers"][0]["staff"].append({**d["tiers"][0]["staff"][0], "id": "Nephew"})
    with pytest.raises(ValidationError, match="letter case"):
        config_from_dict(d)
