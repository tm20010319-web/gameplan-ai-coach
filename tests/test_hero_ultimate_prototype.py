import numpy as np
import pytest

from gameplan.skills.hero_ultimate_prototype import EventTracker, causal_clips
from scripts.hero_ultimate_experiment import (
    evaluate_events, expanded_backgrounds, expanded_events, labels_for, knowledge_record,
)


def test_single_spike_does_not_trigger_or_invent_cast_time():
    tracker = EventTracker(['background', '貂蝉'])
    assert tracker.update(0, [.01, .99]) is None
    assert tracker.update(.13, [.99, .01]) is None
    assert tracker.update(.26, [.01, .99]) is None
    event = tracker.update(.39, [.01, .99])
    assert event['first_seen_s'] == .26
    assert event['cast_time_s'] is None
    assert event['team'] == 'unknown'


def test_ongoing_effect_deduplicates_and_rearms_after_absence():
    tracker = EventTracker(['background', '吕布'])
    events = [tracker.update(i/8, [.01, .99]) for i in range(40)]
    assert len([e for e in events if e]) == 1
    for i in range(40, 50):
        assert tracker.update(i/8, [.99, .01]) is None
    assert tracker.update(50/8, [.01, .99]) is None
    assert tracker.update(51/8, [.01, .99])['hero'] == '吕布'


def test_discontinuous_samples_do_not_confirm_each_other():
    tracker = EventTracker(['background', '虞姬'])
    assert tracker.update(0, [.01, .99]) is None
    assert tracker.update(5, [.01, .99]) is None
    with pytest.raises(ValueError, match='Reset'):
        tracker.update(1, [.01, .99])


def test_causal_window_never_uses_future_frames():
    frames = np.zeros((8, 64, 96, 3), dtype=np.uint8)
    frames[4:] = 255
    clips = causal_clips(frames)
    assert clips[:4].max() == 0
    assert clips[4, -3:].min() == 1
    assert clips[4, :-3].max() == 0


def test_unknown_intervals_are_excluded_instead_of_negative():
    row = {'background_intervals_s': [[0, 1]],
           'events': [{'hero': '吕布', 'visible_interval_s': [2, 3]}]}
    assert labels_for(np.array([0, 1.5, 2.5, 4]), row, ['background', '吕布']).tolist() == [0, -1, 1, -1]


def test_compact_full_match_events_expand_without_relabeling_each_frame():
    row = {'event_hero': '妲己', 'event_onsets_s': [10, 20],
           'event_window_s': [-.25, .75], 'onset_uncertainty_s': .2,
           'background_before_events_s': [-3, -1]}
    assert expanded_events(row) == [
        {'hero': '妲己', 'visible_interval_s': [9.75, 10.75],
         'onset_s': 9.75, 'onset_uncertainty_s': .2},
        {'hero': '妲己', 'visible_interval_s': [19.75, 20.75],
         'onset_s': 19.75, 'onset_uncertainty_s': .2},
    ]
    assert expanded_backgrounds(row) == [[7, 9], [17, 19]]


def test_full_match_background_sampling_keeps_events_positive():
    times = np.arange(0, 6, .125)
    row = {'event_hero': '妲己', 'event_onsets_s': [3], 'event_window_s': [-.25, .5],
           'background_intervals_s': [], 'background_sample_period_s': 1,
           'background_sample_range_s': [0, 5]}
    labels = labels_for(times, row, ['background', '妲己'])
    assert labels[np.argmin(abs(times - 0))] == 0
    assert labels[np.argmin(abs(times - 2))] == 0
    assert labels[np.argmin(abs(times - 3))] == 1
    assert labels[np.argmin(abs(times - 5))] == 0


def test_duplicate_and_wrong_hero_are_counted_as_false_events():
    refs = [{'hero': '吕布', 'visible_interval_s': [2, 4], 'onset_s': 2}]
    events = [{'hero': h, 'first_seen_s': t} for h, t in [('虞姬', 2), ('吕布', 2.1), ('吕布', 3)]]
    report = evaluate_events(events, refs)
    assert len(report['matched']) == 1
    assert len(report['unmatched_or_duplicate_predictions']) == 2
    assert not report['missed']


def test_catalog_base_value_never_becomes_unverified_remaining_time():
    catalog = {'heroes': [{'hero': '吕布', 'skills': [
        {'slot': 3, 'name': '魔神降世', 'base_cooldowns_s': [50]}]}]}
    result = knowledge_record('吕布', catalog)
    assert result['base_cooldowns_s'] == [50]
    assert result['remaining_cooldown_s'] is None
    assert result['ready_at_s'] is None
