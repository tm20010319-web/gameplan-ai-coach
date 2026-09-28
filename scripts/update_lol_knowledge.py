"""Build an atomic, versioned LoL PC snapshot from Riot and Tencent public data.

Run with the project Python. Downloads are cached by Data Dragon version in work/.
curl uses the Windows certificate store; TLS verification is never disabled.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = "https://ddragon.leagueoflegends.com/api/versions.json"
ALIASES = "https://game.gtimg.cn/images/lol/act/img/js/heroList/hero_list.js"
ROLE_NAMES = {"Fighter": "战士", "Tank": "坦克", "Mage": "法师", "Assassin": "刺客", "Marksman": "射手", "Support": "辅助"}


def fetch(url):
    result = subprocess.run(
        ["curl.exe" if os.name == "nt" else "curl", "--fail", "--silent", "--show-error",
         "--retry", "2", "--connect-timeout", "15", "--max-time", "45", url],
        capture_output=True, check=True, timeout=150,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return json.loads(result.stdout)


def clean(value):
    value = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.I)
    return unescape(re.sub(r"<[^>]+>", "", value)).strip()


# These are editorial item pools, not a ranked build or a claim about win rate.
# Every item is checked against this snapshot's purchasable Summoner's Rift items.
PROFILES = {
    "mage": ("法术爆发", ["6655", "3020", "3089", "3135", "3157"], "有法力消耗的法师可考虑法力续航；随后按敌方魔抗和突进威胁选择法穿或保命。", "保持施法距离，先消耗或等待控制命中，再交主要爆发；关键技能空掉后后撤。"),
    "ap": ("法术伤害与进场", ["3152", "3020", "3089", "3135", "3157"], "法强与法穿为输出方向；需要近身时考虑进场工具和保命装备。无蓝条英雄避免为法力属性付费。", "先确认侧翼视野与退出路线，等敌方控制使用后再接近后排，避免正面承受第一轮伤害。"),
    "ap_dps": ("持续法术输出", ["3116", "6653", "3089", "3135", "3157"], "持续伤害可考虑灼烧、减速或生存；按技能是否适配装备效果选择，不必同时购买。", "围绕持续伤害区域拉扯，延长交战时间；保留自保技能应对贴身威胁。"),
    "tank": ("前排承伤", ["3068", "3047", "3065", "3075", "3143"], "按敌方主要伤害分配护甲与魔抗；需要抗暴击、减治疗时再选对应装备。", "开团前确认队友距离与视野；无法接近后排时保留控制保护己方输出。"),
    "fighter": ("物理战士", ["3071", "3053", "6333", "3047", "3065"], "围绕攻击力、生命值和持续作战；面对魔法爆发时调整魔抗，避免机械照抄。", "用兵线与等级窗口换血，进场前留意敌方控制；团战在切入与保护后排之间作选择。"),
    "spellblade": ("普攻与技能穿插", ["3078", "3053", "6333", "3047", "3065"], "适合频繁穿插普攻触发咒刃的打法；根据经济和对手调整防御装备。", "技能之间穿插普攻，注意被动和强化攻击窗口；边线推进前确认敌人位置。"),
    "ezreal": ("技能命中与咒刃", ["3078", "3004", "3161", "6694", "3158"], "围绕 Q 的攻击特效、咒刃和魔宗法力成长选择；注意叠层时间与保命需求。", "利用 Q 远程消耗并穿插普攻，E 优先用于躲控制；不要为了单次伤害主动位移进入敌方包围。"),
    "crit": ("暴击输出", ["3031", "3094", "3036", "3072", "3006"], "以暴击与攻击力为方向；穿甲、续航和攻速按英雄机制及对局补齐，列表不代表购买顺序。", "优先安全攻击最近目标，与前排保持距离；敌方突进未露面时不要为追击离开保护范围。"),
    "onhit": ("攻击特效", ["3153", "3124", "3115", "3091", "3006"], "按英雄的物理、魔法与攻击特效收益选择；纳什之牙仅用于法强收益合适的方案。", "在安全距离持续普攻叠加特效，保留位移躲关键控制；提前布置资源团的输出位置。"),
    "lethality": ("物理爆发", ["3142", "6692", "6694", "3814", "3158"], "脆皮目标优先考虑穿甲；对护甲成长、控制与持续作战压力调整穿透和保命。", "利用视野差寻找落单目标，先观察关键控制再切入；一轮伤害后及时退出等待技能。"),
    "enchanter": ("治疗与护盾辅助", ["6617", "3504", "3107", "3222", "3158"], "根据治疗、护盾和队友伤害类型选择辅助装备；先完成辅助任务并保证视野预算。", "站在能保护核心又不被先手的位置，保留保护技能处理突进；做视野时与队友同行。"),
    "utility": ("功能型辅助", ["2065", "3107", "3222", "3158"], "按团队需要选择移速、救援和解控工具，优先保证技能急速与视野预算。", "利用控制和移速变化保护队友或协助追击；关键保护技能保留给最容易被集火的目标。"),
    "engage": ("开团与保护辅助", ["3190", "3109", "3050", "3111", "3047"], "低经济优先团队保护和抗性；两双鞋是替代选择，只购买适合本局的一双。", "先控制视野再寻找开团角度；己方核心缺少保护时，控制技能优先留给敌方突进。"),
}
OVERRIDES = {}
for profile, names in {
    "ap": "Akali Katarina Kennen Rumble Vladimir Mordekaiser Fizz Ekko Diana Evelynn Gragas Sylas Nidalee Elise Gwen Shyvana",
    "ap_dps": "Singed Cassiopeia Malzahar Brand Swain Lillia AurelionSol Zyra",
    "onhit": "Vayne KogMaw Varus Kalista Kaisa Kayle MasterYi Belveth Teemo",
    "spellblade": "Jax Camille Fiora Irelia Nasus Trundle Volibear Yorick Udyr Vi XinZhao Hecarim",
    "crit": "Yasuo Yone Tryndamere Gangplank Nilah Graves Kindred Senna",
    "ezreal": "Ezreal",
    "mage": "Kassadin Leblanc Morgana",
    "utility": "Zilean",
    "lethality": "Zed Talon Qiyana Khazix Rengar Pyke Naafiri Shaco Jayce Pantheon",
    "enchanter": "Soraka Sona Nami Janna Lulu Yuumi Milio Renata Ivern Karma Seraphine",
    "engage": "Leona Nautilus Rell Alistar Braum Thresh Blitzcrank Rakan Taric Bard",
    "tank": "Malphite Maokai Ornn Sion Chogath DrMundo Zac Sejuani Amumu Rammus Shen KSante TahmKench Nunu Poppy Skarner",
    "fighter": "Aatrox Darius Garen Olaf Renekton Riven Sett Kled Illaoi Urgot Wukong MonkeyKing LeeSin Nocturne RekSai Viego Kayn Ambessa Briar",
}.items():
    OVERRIDES.update({name: profile for name in names.split()})


def profile_for(hero):
    if hero["id"] in OVERRIDES:
        return OVERRIDES[hero["id"]]
    if hero["tags"][0] == "Assassin" and "Mage" in hero["tags"]:
        return "ap"
    return {"Mage": "mage", "Tank": "tank", "Fighter": "fighter", "Marksman": "crit", "Assassin": "lethality", "Support": "enchanter"}[hero["tags"][0]]


def normalize_hero(hero, aliases, items, base):
    profile = profile_for(hero)
    label, ids, build_note, play_note = PROFILES[profile]
    valid_ids = [key for key in ids if key in items]
    if profile == "onhit" and hero["id"] not in {"Kaisa", "Kayle", "Teemo"}:
        valid_ids = [key for key in valid_ids if key != "3115"]
    if hero["id"] == "Yuumi":
        valid_ids = [key for key in valid_ids if key != "3158"]
    if not valid_ids:
        raise ValueError(f"No valid items for {hero['id']}")
    alias = aliases.get(str(hero["key"]), {})
    skills = [{"slot": "P", "name": hero["passive"]["name"], "description": clean(hero["passive"]["description"]),
               "cooldowns": [], "cooldown_text": "被动机制：请参照技能说明", "special": True}]
    for slot, spell in zip("QWER", hero["spells"]):
        values = spell.get("cooldown", [])
        special = not values or any(value <= 0 for value in values)
        # Attack-speed scaling, ammunition, forms and weapon kits need more than a base array.
        special = special or hero["id"] in {"Yasuo", "Yone", "Zeri", "Aphelios", "Jayce", "Nidalee", "Elise", "Hwei", "Udyr", "Shyvana"} or spell.get("maxammo", "-1") not in ("-1", -1)
        cooldown_text = " / ".join(f"{value:g}" if value > 0 else "待核对" for value in values) + " 秒" if values else "官方未提供固定冷却"
        if values and all(value <= 0 for value in values):
            cooldown_text = "官方字段为 0，固定冷却待核对；不代表无冷却"
        skills.append({"slot": slot, "name": spell["name"], "description": clean(spell["description"]),
                       "cooldowns": values, "cooldown_text": cooldown_text,
                       "special": special})
    def tips(field):
        result = [clean(tip).lstrip("- ") for tip in hero.get(field, []) if clean(tip).strip("- ")]
        # Riot's current data file still includes this removed pre-9.14 mechanic.
        if hero["id"] == "Aatrox":
            result = [tip for tip in result if "复活" not in tip]
        return result
    return {
        "id": hero["id"], "key": hero["key"], "name": alias.get("title") or hero["title"],
        "title": alias.get("name") or hero["name"], "roles": [ROLE_NAMES[tag] for tag in hero["tags"]],
        "aliases": sorted(set(filter(None, alias.get("keywords", "").split(",")))),
        "description": clean(hero.get("lore")), "skills": skills,
        "ally_tips": tips("allytips"), "enemy_tips": tips("enemytips"),
        "tips_review_note": "官网旧技巧中关于大招复活的过期说明已剔除。" if hero["id"] == "Aatrox" else "",
        "play_reference": {"label": label, "text": play_note, "kind": "项目整理的类型打法，结合英雄技能使用"},
        "build_reference": {"label": label, "item_ids": valid_ids, "note": build_note,
                            "kind": "项目整理的可选装备池；非官方推荐、非购买顺序、未经当前版本胜率验证", "omitted_item_ids": [key for key in ids if key not in items]},
        "source_url": base + "champion/" + hero["id"] + ".json",
        "official_url": "https://lol.qq.com/data/info-defail.shtml?id=" + hero["id"],
    }


def validate(catalog):
    heroes = catalog["heroes"]
    if len(heroes) != catalog["expected_heroes"] or len({h["id"] for h in heroes}) != len(heroes):
        raise ValueError("Incomplete or duplicate hero roster")
    for hero in heroes:
        if [s["slot"] for s in hero["skills"]] != list("PQWER"):
            raise ValueError("Incomplete skill slots: " + hero["id"])
        if not all(s["name"] and s["description"] for s in hero["skills"]):
            raise ValueError("Missing skill text: " + hero["id"])
        if not hero["build_reference"]["item_ids"] or any(key not in catalog["items"] for key in hero["build_reference"]["item_ids"]):
            raise ValueError("Invalid build reference: " + hero["id"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Pinned Data Dragon version; default: latest published")
    args = parser.parse_args()
    version = args.version or fetch(VERSIONS)[0]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid version")
    base = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/zh_CN/"
    cache = ROOT / "work" / "lol-sources" / version
    cache.mkdir(parents=True, exist_ok=True)

    def cached(path):
        target = cache / path.replace("/", "_")
        if target.exists():
            return json.loads(target.read_text(encoding="utf-8"))
        data = fetch(base + path)
        target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    roster = cached("champion.json")
    item_data = cached("item.json")
    alias_data = fetch(ALIASES)
    aliases = {h["heroId"]: h for h in alias_data["hero"]}
    items = {key: {"id": key, "name": row["name"], "description": clean(row["description"]),
                   "gold": row["gold"]["total"], "tags": row.get("tags", [])}
             for key, row in item_data["data"].items()
             if row.get("maps", {}).get("11") and row.get("gold", {}).get("purchasable") and not row.get("inStore") is False and not row.get("requiredAlly") and not row.get("requiredChampion")}

    def download(hero_id):
        data = cached("champion/" + hero_id + ".json")
        if data["version"] != version or set(data["data"]) != {hero_id}:
            raise ValueError("Hero source identity/version mismatch")
        return normalize_hero(data["data"][hero_id], aliases, items, base)

    with ThreadPoolExecutor(max_workers=6) as pool:
        heroes = list(pool.map(download, sorted(roster["data"])))
    catalog = {"game": "lol-pc", "version": version, "locale": "zh_CN",
               "fetched_at": datetime.now(timezone.utc).isoformat(), "expected_heroes": len(roster["data"]),
               "sources": {"roster": base + "champion.json", "items": base + "item.json", "aliases": ALIASES},
               "alias_version": alias_data.get("version"), "heroes": heroes, "items": items,
               "note": "Riot Data Dragon 中文资料快照；国服上线时间可能不同。官方技巧可能滞后于重做或补丁。基础冷却未计技能急速、等级以外的缩减、充能、形态切换与刷新。"}
    validate(catalog)
    output = ROOT / "resources/knowledge/lol_catalog.json"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, suffix=".tmp", delete=False) as handle:
        json.dump(catalog, handle, ensure_ascii=False, indent=2)
        temporary = handle.name
    os.replace(temporary, output)
    print(json.dumps({"version": version, "heroes": len(heroes), "items": len(items), "output": str(output)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
