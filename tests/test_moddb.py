"""Тесты ядра (без Windows): python -m pytest tests  или  python tests/test_moddb.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from moddb import ModDB, Requirement, check_rule, match_lines, parse_item_text  # noqa: E402

DB = ModDB(log=lambda *a: None)


def sample(name, extra=""):
    text = open(os.path.join(HERE, "samples", name), encoding="utf-8").read()
    return text.replace("Hunter Item", "Hunter Item" + extra) if extra else text


def test_tiers_match_game():
    ctx = parse_item_text(DB, sample("boots_hunter.txt"))
    got = [m.describe() for m in match_lines(ctx, ["+19% to Chaos Resistance", "+33 to maximum Mana",
                                                   "+7% to Item Rarity"])]
    assert got == ["P T9: +33 to maximum Mana", "S T2: +7% to Item Rarity"]   # implicit skipped


def test_ocr_noise_and_elevated():
    ctx = parse_item_text(DB, sample("boots_hunter.txt", "\nRedeemer Item"))
    ms = match_lines(ctx, ["+19% Tb CHAOS RESISTAOCE",
                           "You have Tailwind if you have dealt a Critica1 Strike Recentiy",
                           "10% chance to gain Onslaught for 4 seconds on Kill",
                           "7% increased Attack, Cast and Movement Speed while you have Onslaught"])
    d = [m.describe() for m in ms]
    assert any("ELEVATED" in x and "Onslaught" in x for x in d)
    assert any("Tailwind" in x and "ELEVATED" not in x for x in d)
    assert not any("CHAOS" in x for x in d)


def test_rule_modes():
    ctx = parse_item_text(DB, sample("boots_hunter.txt"))
    life = ("IncreasedLife", "", "prefix", False)
    ms = ("MovementVelocity", "", "prefix", False)
    m = match_lines(ctx, ["+83 TO MAXIMUM LIFE", "+37% TO LIGHTNING RESISTANCE"])
    reqs = [Requirement(life, 6), Requirement(ms, 6)]
    assert check_rule(m, reqs, "any") and not check_rule(m, reqs, "all")


def test_cluster_enchant_pool_and_chance():
    ctx = parse_item_text(DB, sample("small_cluster_reservation.txt"))
    assert ctx.enchant.startswith("6% increased Mana Reservation Efficiency")
    effect = next(mods for k, mods in ctx.groups.items()
                  if "Small Passive Skills have" in mods[0].text)
    assert effect[0].text.endswith("35% increased Effect") and effect[0].level == 84
    assert 0.015 < effect[0].chance < 0.025
    got = match_lines(ctx, ["1 Added Passive Skill is Purposeful Harbinger"])
    assert got and "Purposeful Harbinger" in got[0].describe()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
