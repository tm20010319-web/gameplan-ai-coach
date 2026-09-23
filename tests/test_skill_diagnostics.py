import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from gameplan.core.models import VisionRequest
from gameplan.monitoring import skill_diagnostics as diagnostics
from test_scoreboard_skill_history import encoded, scene, reply
from test_enemy_casts import client


@pytest.fixture
def recording(tmp_path, monkeypatch):
    monkeypatch.setenv('GAMEPLAN_SKILL_DIAGNOSTICS', '1')
    monkeypatch.setattr(diagnostics, 'ROOT', tmp_path)
    return tmp_path


def test_only_enabled_live_skill_requests_are_saved(monkeypatch):
    req = VisionRequest(image_base64=encoded('navy'), input_kind='live', focus='skills')
    monkeypatch.setenv('GAMEPLAN_SKILL_DIAGNOSTICS', '0')
    assert diagnostics.begin(req) is None
    monkeypatch.setenv('GAMEPLAN_SKILL_DIAGNOSTICS', '1')
    assert diagnostics.begin(req) is not None
    assert diagnostics.begin(req.model_copy(update={'input_kind': 'video'})) is None
    assert diagnostics.begin(req.model_copy(update={'focus': 'heroes'})) is None


def test_route_saves_actual_submitted_images_model_reply_and_rejection(recording, client):
    answer = reply()
    content = json.loads(answer['message']['content'])
    content['events'][0]['actor_id'] = 10  # A high-confidence answer for a nonexistent actor.
    answer['message']['content'] = json.dumps(content)
    current = scene()
    import time
    now = time.time()
    from gameplan.skills.auto_skill_monitor import EventTracker
    import gameplan.monitoring.monitor_runtime as runtime
    tracker = EventTracker(); tracker.phase = 'in_game'; tracker.roster_verified = True
    tracker.allies = ['艾琳']; tracker.enemies = ['妲己']; tracker.last_seen = now
    runtime.trackers['diagnostic-test'] = tracker
    with patch('gameplan.skills.combat_evidence.read_scene', return_value=current), \
         patch('gameplan.skills.keyframe_casts.visible_actors', return_value=[{'hero': '妲己', 'x': .6, 'y': .4}]), \
         patch('gameplan.ai.integrations.post_json', return_value=answer), \
         patch('gameplan.vision.summoner_icons.read_equipment', return_value=[]):
        response = client.post('/api/vision/observe', json={
            'match_id': 'diagnostic-test', 'image_base64': encoded('maroon'),
            'captured_at': now, 'input_kind': 'live', 'focus': 'skills'})
    assert response.status_code == 200, response.text
    scan = response.json()['skill_scan']
    assert scan['diagnostic_status'] == 'saved'
    with zipfile.ZipFile(recording / (scan['diagnostic_id'] + '.zip')) as archive:
        data = json.loads(archive.read('trace.json'))
        assert {'F0.png', 'model-contact-sheet.jpg', 'trace.json'} <= set(archive.namelist())
        assert data['frames'][0]['captured_at'] == now
        assert data['skill_model_answer']['message'] == answer['message']
        assert data['skill_targets']['actors'][0]['hero'] == '妲己'
        assert data['response']['skill_scan']['rejected_candidates']['actor_identity_mismatch'] == 1
        assert data['response']['enemy_skill_timers'] == []
        assert 'image_base64' not in data['request']
    assert diagnostics.active_trace.get() is None


def test_diagnostic_disk_failure_does_not_change_successful_response(recording, client):
    with patch('gameplan.web.monitor_app.observe', return_value={'skill_scan': {}, 'note': 'ok'}), \
         patch.object(Path, 'mkdir', side_effect=OSError('disk full')):
        response = client.post('/api/vision/observe', json={
            'image_base64': encoded('navy'), 'input_kind': 'live', 'focus': 'skills'})
    assert response.status_code == 200
    assert response.json()['skill_scan']['diagnostic_status'] == 'write_failed'


@pytest.mark.parametrize('bound', ['count', 'bytes', 'age'])
def test_rotation_is_bounded_and_leaves_unrelated_files(recording, monkeypatch, bound):
    (recording / 'keep.txt').write_text('keep')
    archives = [recording / f'cast-{i}-00000000.zip' for i in (1, 2, 3)]
    import os
    import time
    for i, path in enumerate(archives):
        path.write_bytes(b'1234567890')
        timestamp = time.time() - 3 + i
        os.utime(path, (timestamp, timestamp))
    if bound == 'count':
        monkeypatch.setattr(diagnostics, 'MAX_BATCHES', 2)
    elif bound == 'bytes':
        monkeypatch.setattr(diagnostics, 'MAX_BYTES', 20)
    else:
        old = time.time() - diagnostics.MAX_AGE_S - 1
        os.utime(archives[0], (old, old))
    diagnostics.prune()
    assert not archives[0].exists()
    assert all(path.exists() for path in archives[1:])
    assert (recording / 'keep.txt').read_text() == 'keep'
