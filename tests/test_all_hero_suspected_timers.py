"""Catalog-wide routing/clock contract; synthetic events do not measure vision recall."""
import pytest

from gameplan.skills.auto_skill_monitor import EventTracker
from gameplan.skills.monitor_policy import recognition_reference


@pytest.mark.parametrize('hero', list(EventTracker().catalog))
def test_every_catalog_hero_can_report_named_suspected_ultimate(hero):
    tracker = EventTracker()
    reference = recognition_reference(hero)
    assert reference is not None
    tracker.ingest_suspicions([{'hero': hero, 'skill': reference['ultimate'],
        'frame_index': 0, 'confidence': .8, 'evidence': '红血条角色周围可见持续特效，身份尚未确认',
        'reason': 'visual_identity_unconfirmed', 'event_type': 'keyframe'}],
        enemies=[hero], allies=[], frame_times=[100], now=100)
    first = tracker.estimate_snapshot(100)[0]
    assert first['hero'] == hero and first['confirmed'] is False
    assert first['identity_confirmed'] is False and not tracker.events
    if hero == '朵莉亚':
        assert first['remaining_range_s'] is None  # Depends on the refreshed skill.
    else:
        later = tracker.estimate_snapshot(100.5)[0]
        assert first['remaining_range_s'][1] > later['remaining_range_s'][1]


def test_unknown_or_out_of_roster_hero_never_creates_an_estimate():
    tracker = EventTracker()
    tracker.ingest_suspicions([{'hero': '未知敌方', 'skill': '大招', 'frame_index': 0,
        'confidence': .9, 'evidence': '可见特效', 'event_type': 'keyframe'}],
        enemies=['小乔'], allies=[], frame_times=[100], now=100)
    assert tracker.estimate_snapshot(100) == []
