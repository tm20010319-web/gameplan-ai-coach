"""Provisional knowledge-based cooldowns, kept outside confirmed cast history."""
from uuid import uuid4

from gameplan.skills.monitor_policy import cooldown_values


class SuspectedCooldowns:
    def __init__(self):
        self.records = {}

    def observe(self, raw, candidate, frame_times, item, cooldown, *, values=None):
        key = (candidate['hero'], candidate['skill'])
        stamp = candidate['last_seen_at']
        previous = self.records.get(key)
        # Repeated observations of the same uncertain cast never reset its clock.
        if previous and previous['status'] in ('estimated', 'unknown') and previous['expires_at'] > stamp:
            return
        index = raw['frame_index']
        reason = candidate['reason']
        onset = (raw.get('event_type') == 'cast_start' and index > 0
                 and 0 <= frame_times[index]-frame_times[index-1] <= 1.5)
        identity = reason not in ('visual_identity_unconfirmed', 'onset_identity_unconfirmed')
        blocked = reason in ('ongoing_onset_unknown', 'onset_before_sequence', 'flash_motion_unconfirmed')
        # The user accepts tentative countdowns without a known onset or
        # confirmed nickname. Anchor those to the observed source frame, never
        # claim it is the actual cast time or copy them into confirmed history.
        observed = candidate['is_ultimate'] or raw.get('event_type') == 'keyframe'
        known = (observed or onset and identity and not blocked) and cooldown is not None
        values = (values if values is not None else [cooldown] if not candidate['is_ultimate'] else cooldown_values(item or {})) if known else []
        bounded_onset = onset and not blocked
        low, high = ((frame_times[index-1], frame_times[index]) if bounded_onset else
                     (stamp, stamp) if observed else (None, None))
        if values:
            basis=('疑似释放，尚未确认；' + ('角色身份待确认；' if not identity else '')
                   + ('按观察到的起手区间估算；' if bounded_onset else '从首次看到疑似特效起算，实际起手时间未知；')
                   + '知识库基础冷却范围，未计装备减冷却、刷新和特殊调整')
            status='estimated'
        else:
            basis=('角色身份未核实' if not identity else '起手时间未知' if not onset or blocked
                   else '冷却机制或资料待核实')+'，暂时无法估算'
            status='unknown'
        self.records[key] = {
            'id':uuid4().hex, 'candidate_id':candidate['id'], 'hero':key[0], 'skill':key[1],
            'is_ultimate':candidate['is_ultimate'], 'confirmed':False, 'status':status,
            'captured_at':stamp, 'cast_window_start':low, 'cast_window_end':high,
            'timing_basis':'suspected_onset_interval' if bounded_onset else 'first_visible_candidate',
            'identity_confirmed':identity,
            'cooldown_range_s':[min(values),max(values)] if values else None,
            'cooldown_basis':basis, 'reason':reason,
            'expires_at':stamp+(max(values)+8 if values else 15),
        }
        if len(self.records)>32:
            oldest=min(self.records,key=lambda k:self.records[k]['captured_at'])
            self.records.pop(oldest)

    def retract(self, hero, reason, at, *, ultimate_only=False):
        for value in self.records.values():
            if value['hero']==hero and (not ultimate_only or value['is_ultimate']):
                value.update(status='retracted',cooldown_basis=reason,expires_at=at+15)

    def snapshot(self, now, confirmed):
        self.records={k:r for k,r in self.records.items() if r['expires_at']>now}
        result=[]
        for record in self.records.values():
            if any(e.hero==record['hero'] and e.skill==record['skill'] and
                   e.captured_at>=record['captured_at']-1.5 for e in confirmed):
                record['status']='confirmed'
                continue
            row=dict(record);row['remaining_range_s']=None
            if row['status']=='estimated' and row['cooldown_range_s']:
                low,high=row['cooldown_range_s']
                row['remaining_range_s']=[max(0,low-(now-row['cast_window_start'])),
                                          max(0,high-(now-row['cast_window_end']))]
                if row['remaining_range_s'][1]==0:row['status']='elapsed'
            if row['status']!='confirmed':result.append(row)
        return result
