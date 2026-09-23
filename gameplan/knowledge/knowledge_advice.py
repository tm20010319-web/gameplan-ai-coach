"""Select sourced conditional references, never assert unobserved triggers."""


def select_references(context, visible, lane, known_heroes, limit=2):
    player = context.get('player')
    if not player or visible.get('phase') not in ('loading', 'in_game'):
        return []
    record = next((r for r in context.get('skill_knowledge', []) if r['hero'] == player), None)
    profile = (record or {}).get('coaching_profile') or {}
    present = set(context['group_a'] + context['group_b'])
    readings = visible.get('self_skills') or []
    # Only fresh readings for the selected player can suppress unavailable skills.
    blocked = {r['slot'] for r in readings if r.get('remaining_s', 0) is not None and r['remaining_s'] > 0} if context['scope'] == 'live' and visible.get('player_hero') == player else set()
    selected = []
    for rule in sorted(profile.get('rules', []), key=lambda r: -r.get('priority', 0)):
        if visible['phase'] not in rule.get('phases', ['loading', 'in_game']):
            continue
        if rule.get('lane_required') and rule['lane_required'] != lane:
            continue
        if blocked.intersection(rule.get('basis_slots', [])):
            continue
        text = rule['when'] + rule['action']
        if any(name in text and name not in present for name in known_heroes):
            continue
        selected.append({**rule, 'hero': player, 'conditions_confirmed': False,
                         'source_label': '官网提示' if rule['basis_type'] == 'official_tip_with_application_conditions' else '用户攻略' if rule['basis_type'] == 'user_guide_with_application_conditions' else '第三方攻略'})
        if len(selected) >= limit:
            break
    return selected


def reference_summary(references):
    return ' '.join(f"条件参考（{r['source_label']}）：若{r['when']}，{r['action']}" for r in references)
