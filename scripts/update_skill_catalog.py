"""Import public hero skill references from the Honor of Kings official site."""
import concurrent.futures
import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LIST_URL = "https://pvp.qq.com/web201605/js/herolist.json"
DETAIL_URL = "https://pvp.qq.com/zlkdatasys/zsbd_herolist/{hero_id}.json"
# These heroes have four active buttons; their fourth button is the ultimate.
FOURTH_ULTIMATE = {191, 182, 179, 125, 519}


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "GAMEPLAN/1.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read(3000000)
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("Unknown page encoding")


def plain(value):
    value = re.sub(r"<br\s*/?>|</p>", " ", str(value or ""), flags=re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", value))).strip()


def cooldown_fields(raw, ranks):
    """Retain the official expression; never reduce a conditional CD to a scalar."""
    original = plain(raw) or "官网未提供"
    text = re.sub(r"\s+", "", original).replace("（", "(").replace("）", ")")
    text = re.sub(r"(?:秒|s)$", "", text, flags=re.I)
    result = {"cooldown_text": original, "base_cooldowns_s": [],
              "cooldown_kind": "unknown", "cooldown_formula": None}
    number = r"\d+(?:\.\d+)?"
    if re.fullmatch(fr"{number}(?:/{number})*", text):
        values = [float(v) for v in text.split('/')]
        result.update(base_cooldowns_s=values,
                      cooldown_kind="no_fixed" if all(v == 0 for v in values) else "per_level" if len(values) > 1 else "fixed")
    else:
        # Official notation: first-rank value minus the decrement per rank-up.
        formula = re.fullmatch(fr"({number})\(?-({number})/Lv\)?", text, re.I)
        dynamic = re.fullmatch(fr"({number}(?:/{number})*)\(\+({number})%目标技能当前冷却\)", text)
        if formula:
            base, delta = map(float, formula.groups())
            values = [round(base - delta * i, 6) for i in range(ranks)]
            if min(values) < 0:
                raise ValueError("Invalid per-rank cooldown")
            result.update(base_cooldowns_s=values, cooldown_kind="level_formula",
                          cooldown_formula={"base_s": base, "decrease_per_rank_s": delta,
                                            "ranks": ranks, "expression": "base_s - (skill_rank - 1) * decrease_per_rank_s",
                                            "note": "按官网1级基值及每次技能升级递减量展开；以游戏内技能等级为准。"})
        elif dynamic:
            result.update(cooldown_kind="dynamic", cooldown_formula={
                "base_component_s": [float(v) for v in dynamic.group(1).split('/')],
                "target_remaining_multiplier": float(dynamic.group(2)) / 100,
                "requires": ["skill_rank", "target_skill_remaining_s"],
                "expression": "base_component_s[skill_rank - 1] + target_remaining_multiplier * target_skill_remaining_s"})
    return result


def has_cooldown_mechanic(description):
    # A shield refresh alone is not a cooldown reset.
    return bool(re.search(r"充能|储存|使用次数|力劲|冷却|(?:刷新|重置|返还).{0,16}技能|技能.{0,16}(?:刷新|重置|返还)|切换形态", description))


def parse_official_skills(data):
    hero_id = int(data['ename'])
    skills = []
    for field in range(1, 11):
        key = f'skill{field}'
        name = plain(data.get(key + '_name'))
        if not name:
            continue
        # Use the documented button order, not an arbitrary digit in image IDs.
        # 18801 and 58170, for example, are both first active skills.
        slot = field - 1
        if hero_id == 525 and data.get('skill5_name') and field in (4, 5):
            slot = 4 if field == 4 else 3  # Mechanical grapple / ultimate collection.
        passive = field in (1, 6)
        ultimate = not passive and slot == (4 if hero_id in FOURTH_ULTIMATE else 3)
        description = plain(data.get(key + '_des'))
        if not description:
            raise ValueError(f'Missing description: {name}')
        cd = cooldown_fields(data.get(key + '_cold'), 3 if ultimate else 6)
        review = ultimate and len(cd['base_cooldowns_s']) > 4
        skills.append({"slot": slot, "source_skill_id": str(data.get(key, '')),
                       "source_field": key, "name": name, "passive": passive,
                       "is_ultimate": ultimate, "description": description,
                       "tips": plain(data.get(key + '_tips')),
                       "cooldown_review_required": review,
                       "cooldown_note": "官网大招冷却字段列出超过4档数值，疑似与普通技能错位；保留原值，暂不用于倒计时。" if review else "",
                       "special_mechanic": has_cooldown_mechanic(description) or cd['cooldown_kind'] == 'dynamic',
                       **cd})
    if len(skills) < 4 or not any(s['is_ultimate'] for s in skills):
        raise ValueError('Incomplete official skill set')
    if len({s['slot'] for s in skills}) != len(skills):
        raise ValueError('Duplicate skill slots')
    return skills


def parse_skills(page):
    entries = re.findall(r'<p\s+class="skill-name"[^>]*>(.*?)</p>\s*<p\s+class="skill-desc"[^>]*>(.*?)</p>', page, re.S)
    skills = []
    for header, description in entries:
        name = re.search(r"<b[^>]*>(.*?)</b>", header, re.S)
        if not name or not plain(name.group(1)):
            continue
        spans = re.findall(r"<span[^>]*>(.*?)</span>", header, re.S)
        cooldown = next((plain(value).split("：", 1)[-1] for value in spans if "冷却" in value), "")
        values = [float(value) for value in cooldown.split("/")] if re.fullmatch(r"\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)*", cooldown) else []
        description = plain(description)
        slot = len(skills)
        skills.append({
            "slot": slot, "name": plain(name.group(1)), "passive": slot == 0,
            "base_cooldowns_s": values, "cooldown_text": cooldown or "官网未提供",
            "description": description,
            "special_mechanic": bool(re.search(r"充能|重置|刷新|返还|减少.{0,12}冷却|冷却.{0,12}减少|切换形态", description)),
        })
    if len(skills) < 2:
        raise ValueError("No complete skill section")
    return skills


def parse_mobile_skills(page):
    entries = re.findall(r'<span\s+class="plus-name"[^>]*data-skillid="(\d+)"[^>]*>(.*?)</span>\s*<span\s+class="plus-value"[^>]*>(.*?)</span>.*?<p\s+class="plus-int"[^>]*>(.*?)</p>', page, re.S)
    skills = []
    for skill_id, name, value, description in entries:
        cd = re.search(r'冷却值[：:]\s*([\d./]+)', plain(value))
        cooldown = cd.group(1) if cd else ''
        slot = int(skill_id[-2])
        desc = plain(description)
        skills.append({'slot': slot, 'name': plain(name), 'passive': slot == 0,
                       'base_cooldowns_s': [float(v) for v in cooldown.split('/')] if cooldown else [],
                       'cooldown_text': cooldown or '官网未提供', 'description': desc,
                       'special_mechanic': bool(re.search(r'充能|重置|刷新|返还|冷却|形态', desc))})
    if len(skills) < 2:
        raise ValueError('No complete mobile skill section')
    return skills


def hero_entry(hero, source_dir=None):
    structured_url = DETAIL_URL.format(hero_id=int(hero['ename']))
    if source_dir is not None:
        path = source_dir / f"{int(hero['ename'])}.json"
        data = json.loads(path.read_text(encoding='utf-8'))
    else:
        try:
            data = json.loads(fetch(structured_url))
        except Exception:
            data = None
    if data is not None:
        if int(data.get('ename', 0)) != int(hero['ename']) or data.get('cname') != hero['cname']:
            raise ValueError('Official hero identity mismatch')
        return {"hero": hero['cname'], "id": int(hero['ename']), "source_url": structured_url,
                "source_kind": "official_structured", "skills": parse_official_skills(data),
                "title": data.get('title', ''), "roles": [int(data[k]) for k in ('hero_type', 'hero_type2') if data.get(k)],
                "status": "official_reference", "fetched_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if source_dir else datetime.now(timezone.utc).isoformat()}
    url = f"https://pvp.qq.com/web201605/herodetail/{int(hero['ename'])}.shtml"
    entry = {"hero": hero["cname"], "id": hero["ename"], "source_url": url}
    def normalized_legacy(skills):
        data = {'ename': hero['ename']}
        for i, skill in enumerate(skills, 1):
            data.update({f'skill{i}': skill.get('source_skill_id', ''), f'skill{i}_name': skill['name'],
                         f'skill{i}_cold': skill['cooldown_text'], f'skill{i}_des': skill['description']})
        return parse_official_skills(data)
    try:
        return {**entry, "roles": [hero[k] for k in ("hero_type", "hero_type2") if hero.get(k)], "title": hero.get("title", ""), "skills": normalized_legacy(parse_skills(fetch(url))), "status": "official_reference", "source_kind": "official_legacy"}
    except Exception as exc:
        mobile_url = f"https://pvp.qq.com/web201605/herodetail/m/{int(hero['ename'])}.html"
        try:
            return {**entry, "source_url": mobile_url, "roles": [hero[k] for k in ("hero_type", "hero_type2") if hero.get(k)],
                    "title": hero.get("title", ""), "skills": normalized_legacy(parse_mobile_skills(fetch(mobile_url))), "status": "official_reference", "source_kind": "official_legacy"}
        except Exception:
            pass
        return {**entry, "roles": [hero[k] for k in ("hero_type", "hero_type2") if hero.get(k)], "title": hero.get("title", ""), "skills": [], "status": "unavailable", "error": type(exc).__name__}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, help='Import an already downloaded official JSON snapshot (herolist.json and ID.json).')
    args = parser.parse_args()
    destination = ROOT / "resources" / "knowledge" / "skill_catalog.json"
    old = json.loads(destination.read_text(encoding="utf-8")) if destination.exists() else {"heroes": []}
    previous = {h["hero"]: h for h in old["heroes"]}
    heroes = json.loads((args.source_dir / 'herolist.json').read_text(encoding='utf-8') if args.source_dir else fetch(LIST_URL))
    if not heroes or len({int(h['ename']) for h in heroes}) != len(heroes) or len({h['cname'] for h in heroes}) != len(heroes):
        raise ValueError('Empty or duplicate official roster')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        entries = list(pool.map(lambda hero: hero_entry(hero, args.source_dir), heroes))
    for item in entries:
        item.setdefault("fetched_at", datetime.now(timezone.utc).isoformat())
        if not item["skills"] and previous.get(item["hero"], {}).get("skills"):
            prior = previous[item["hero"]]
            item.update(prior, status="cached_official_reference",
                        last_refresh_error="Official source unavailable; retained prior source and timestamp")
        for skill in item['skills']:
            if skill.get('cooldown_review_required'):
                prior = previous.get(item['hero'], {})
                prior_skill = next((s for s in prior.get('skills', []) if s['name'] == skill['name']), None)
                if prior_skill and not prior_skill.get('cooldown_review_required'):
                    skill['cooldown_alternatives'] = [{
                        'source_url': prior.get('source_url'), 'fetched_at': prior.get('fetched_at', old.get('fetched_at')),
                        'cooldown_text': prior_skill['cooldown_text'], 'base_cooldowns_s': prior_skill['base_cooldowns_s'],
                        'note': '历史官网条目，仅供核对，不能当作当前版本结论。'}]
                elif prior_skill:
                    skill['cooldown_alternatives'] = prior_skill.get('cooldown_alternatives', [])
    catalog = {
        "schema_version": 2,
        "fetched_at": datetime.now(timezone.utc).isoformat(), "source_url": LIST_URL,
        "game_version": None,
        "detail_source": DETAIL_URL,
        "note": "王者荣耀官网制胜宝典公开技能资料。官网未标注当前游戏版本；保留来源、采集时间和冷却原式，按技能等级列出基础冷却。动态公式、特殊刷新、充能、形态与装备效果不能直接套用普通倒计时。",
        "heroes": entries,
    }
    destination = ROOT / "resources" / "knowledge" / "skill_catalog.json"
    staging = destination.with_suffix(".json.tmp")
    staging.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    staging.replace(destination)
    print(json.dumps({"heroes": len(entries), "with_skills": sum(bool(item["skills"]) for item in entries),
                      "skills": sum(len(item['skills']) for item in entries),
                      "with_ultimate": sum(any(s.get('is_ultimate', s['slot'] == 3) for s in item['skills']) for item in entries),
                      "unavailable": [item["hero"] for item in entries if not item["skills"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
