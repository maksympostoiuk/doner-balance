## Doner Lamar economy loader for Godot 4.x
##
## Loads export/economy.json (format "doner-lamar-economy", format_version 1) and
## implements the same formulas as the Python simulator. Run `self_test()` once
## after loading: it checks the formulas against the test vectors in the file.
##
## Usage (e.g. as an autoload named Economy):
##     Economy.load_file("res://data/economy.json")
##     assert(Economy.self_test())
##     var price := Economy.level_cost(Economy.item("nephew"), 12)
class_name DonerEconomy
extends Node

const FORMAT := "doner-lamar-economy"
const SUPPORTED_VERSION := 1

var data: Dictionary = {}
var _items: Dictionary = {}       # id -> item dictionary
var _item_tier: Dictionary = {}   # id -> tier index (-1 for tap upgrades)
var _item_kind: Dictionary = {}   # id -> "tap" | "staff" | "upgrade" | "manager"


func load_file(path: String) -> bool:
	var text := FileAccess.get_file_as_string(path)
	var parsed = JSON.parse_string(text)
	if typeof(parsed) != TYPE_DICTIONARY:
		push_error("economy: cannot parse %s" % path)
		return false
	if parsed.get("format") != FORMAT or int(parsed.get("format_version", 0)) != SUPPORTED_VERSION:
		push_error("economy: unsupported format %s v%s" % [parsed.get("format"), parsed.get("format_version")])
		return false
	data = parsed
	_index()
	return true


func _index() -> void:
	_items.clear()
	for u in data.tap.upgrades:
		_register(u, -1, "tap")
	for tier in data.tiers:
		var ti: int = int(tier.index)
		for s in tier.staff:
			_register(s, ti, "staff")
		for u in tier.upgrades:
			_register(u, ti, "upgrade")
		for m in tier.managers:
			_register(m, ti, "manager")


func _register(it: Dictionary, ti: int, kind: String) -> void:
	_items[it.id] = it
	_item_tier[it.id] = ti
	_item_kind[it.id] = kind


func item(id: String) -> Dictionary:
	return _items.get(id, {})


# ------------------------------------------------------------------ formulas

## Price of the next level when `level` levels are already owned.
func level_cost(it: Dictionary, level: int) -> float:
	return float(it.base_cost) * pow(float(it.cost_growth), level)


## Price of buying `n` levels starting at `level`.
func bulk_cost(it: Dictionary, level: int, n: int) -> float:
	if n <= 0:
		return 0.0
	var g := float(it.cost_growth)
	return float(it.base_cost) * pow(g, level) * (pow(g, n) - 1.0) / (g - 1.0)


func milestone_mult(level: int) -> float:
	var m := 1.0
	for ms in data.staff_milestones:
		if level >= int(ms.level):
			m *= float(ms.mult)
	return m


func staff_income(it: Dictionary, level: int) -> float:
	if level <= 0:
		return 0.0
	return level * float(it.income) * milestone_mult(level)


func reputation_total(all_time_earnings: float) -> int:
	var p: Dictionary = data.prestige
	if all_time_earnings <= 0.0:
		return 0
	return int(floor(float(p.scale) * pow(all_time_earnings / float(p.base_requirement), float(p.exponent))))


# ------------------------------------------------------------------ state-based income
# `state` is a Dictionary: {tiers_owned:int, levels:{id:int}, managers:[id], reputation:int,
#                           event_mult:float (optional, product of active NPC modifiers)}

func _lv(state: Dictionary, id: String) -> int:
	return int(state.levels.get(id, 0))


func tier_income(state: Dictionary, ti: int) -> float:
	if ti >= int(state.tiers_owned):
		return 0.0
	var tier: Dictionary = data.tiers[ti]
	var base := 0.0
	for s in tier.staff:
		base += staff_income(s, _lv(state, s.id))
	if base == 0.0:
		return 0.0
	var inc := 1.0
	var spd := 1.0
	for u in tier.upgrades:
		if u.kind == "income":
			inc += float(u.value) * _lv(state, u.id)
		else:
			spd += float(u.value) * _lv(state, u.id)
	var mgr := 1.0
	for m in tier.managers:
		if m.id in state.managers:
			mgr *= float(m.income_mult)
	return base * inc * spd * mgr


func global_mult(state: Dictionary) -> float:
	var m := 1.0
	for ti in range(int(state.tiers_owned)):
		m *= float(data.tiers[ti].global_income_mult)
	if data.prestige.enabled:
		m *= 1.0 + int(state.get("reputation", 0)) * float(data.prestige.bonus_per_point)
	return m * float(state.get("event_mult", 1.0))


func auto_income(state: Dictionary) -> float:
	var total := 0.0
	for ti in range(int(state.tiers_owned)):
		total += tier_income(state, ti)
	return total * global_mult(state)


func tap_value(state: Dictionary) -> float:
	var flat := float(data.tap.base_value)
	var pct := 1.0
	var share := 0.0
	for u in data.tap.upgrades:
		var lv := _lv(state, u.id)
		match u.kind:
			"flat": flat += float(u.value) * lv
			"pct": pct += float(u.value) * lv
			"income_share": share += float(u.value) * lv
	var tier_mult := float(data.tiers[int(state.tiers_owned) - 1].tap_mult)
	return flat * pct * tier_mult * global_mult(state) + share * auto_income(state)


func _managed(state: Dictionary, ti: int) -> bool:
	if not data.offline.requires_manager:
		return true
	for m in data.tiers[ti].managers:
		if m.id in state.managers and m.enables_offline:
			return true
	return false


func offline_cap_hours(state: Dictionary) -> float:
	var cap := float(data.offline.cap_hours)
	for mid in state.managers:
		cap += float(item(mid).get("offline_cap_hours_add", 0.0))
	return cap


func offline_income_per_sec(state: Dictionary) -> float:
	var total := 0.0
	for ti in range(int(state.tiers_owned)):
		if _managed(state, ti):
			total += tier_income(state, ti)
	return total * global_mult(state) * float(data.offline.efficiency)


## Coins to credit when the player returns after `seconds_away`.
func offline_earnings(state: Dictionary, seconds_away: float) -> float:
	return offline_income_per_sec(state) * min(seconds_away, offline_cap_hours(state) * 3600.0)


# ------------------------------------------------------------------ verification

func _close(a: float, b: float) -> bool:
	return abs(a - b) <= max(1e-9, abs(b) * 1e-9)


func self_test() -> bool:
	var ok := true
	var tv: Dictionary = data.test_vectors
	for v in tv.level_cost:
		if not _close(level_cost(item(v.item), int(v.level)), float(v.cost)):
			push_error("level_cost mismatch for %s L%d" % [v.item, v.level]); ok = false
	for v in tv.staff_income:
		if not _close(staff_income(item(v.item), int(v.level)), float(v.income)):
			push_error("staff_income mismatch for %s L%d" % [v.item, v.level]); ok = false
	for v in tv.reputation:
		if reputation_total(float(v.all_time_earnings)) != int(v.reputation_total):
			push_error("reputation mismatch"); ok = false
	var s: Dictionary = tv.sample_state
	var e: Dictionary = s.expected
	if not _close(auto_income(s), float(e.auto_income)): push_error("auto_income mismatch"); ok = false
	if not _close(tap_value(s), float(e.tap_value)): push_error("tap_value mismatch"); ok = false
	if not _close(offline_income_per_sec(s), float(e.offline_income_per_sec)):
		push_error("offline income mismatch"); ok = false
	return ok
