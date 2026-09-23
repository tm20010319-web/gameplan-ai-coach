import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from gameplan.ai.advisor import KNOWN_HEROES
from gameplan.skills.combat_evidence import read_scene, merge_bindings
from gameplan.skills.grounded_casts import detect, final_json
from gameplan.core.models import VisionRequest

FIXTURES = Path(__file__).parent / "fixtures"
IDENTITIES = [{"hero": "敖隐", "nickname": "好心情爱犯困", "side": "enemy_roster"},
              {"hero": "艾琳", "nickname": "深蓝北斗星", "side": "ally_roster"}]


def frame(which):
    pixels = (FIXTURES / "combat" / f"aoyin-{which}.jpg").read_bytes()
    readings = json.loads((FIXTURES / "combat" / f"aoyin-{which}-ocr.json").read_text(encoding="utf-8"))
    return pixels, read_scene(pixels, KNOWN_HEROES, IDENTITIES, readings=readings)


def test_local_name_binding_recovers_enemy_identity_and_level_without_hero_name_guessing():
    _, result = frame("before")
    # The combat identity chain now accepts enemy red-bar evidence only.
    assert {t["hero"] for t in result["targets"]} == {"敖隐"}
    assert next(v["level"] for v in result["hero_levels"] if v["hero"] == "敖隐") == 5
    pixels = (FIXTURES / "combat" / "aoyin-before.jpg").read_bytes()
    readings = json.loads((FIXTURES / "combat" / "aoyin-before-ocr.json").read_text(encoding="utf-8"))
    assert read_scene(pixels, KNOWN_HEROES, [], readings=readings)["targets"] == []


def test_one_nickname_cannot_bind_two_heroes():
    result = merge_bindings(IDENTITIES, [{"hero": "孙策", "nickname": "好心情爱犯困", "side": "enemy_roster"}])
    assert [b["hero"] for b in result] == ["艾琳"]


def test_scoreboard_locally_reads_nickname_and_portrait_level_not_skill_countdown():
    pixels = (FIXTURES / "summoner" / "scoreboard.jpg").read_bytes()
    readings = json.loads((FIXTURES / "summoner" / "scoreboard-ocr.json").read_text(encoding="utf-8"))
    result = read_scene(pixels, KNOWN_HEROES, readings=readings)
    assert result["panel"]
    assert next(b["nickname"] for b in result["bindings"] if b["hero"] == "敖隐") == "好心情爱犯困"
    assert {v["hero"]: v["level"] for v in result["hero_levels"]}["孙策"] == 9
    assert {v["hero"]: v["level"] for v in result["hero_levels"]}["艾琳"] == 8


@pytest.mark.parametrize("response", [
    {"done_reason": "stop", "message": {"content": "", "thinking": '{"state":"cast_start"}'}},
    {"done_reason": "length", "message": {"content": '{"state":"cast_start"}'}},
])
def test_thinking_json_or_truncated_answers_never_count_as_casts(response):
    with pytest.raises(ValueError):
        final_json(response)


@pytest.mark.parametrize("state,index,accepted,active", [
    ("cast_start", 1, True, False), ("cast_start", 0, False, True),
    ("ongoing", 0, False, True), ("none", 1, False, False), ("uncertain", 1, False, False),
])
def test_targeted_detection_keeps_original_frame_index_and_ongoing_never_starts_timer(state, index, accepted, active):
    before, earlier = frame("before"); after, scene = frame("after")
    req = VisionRequest(image_base64=base64.b64encode(after).decode(), captured_at=100, focus="skills",
        recent_frames=[{"image_base64": base64.b64encode(before).decode(), "captured_at": t} for t in (98, 99)])
    candidate = {"skill": "穷乎玄间", "state": state, "frame_index": index, "confidence": .96,
                 "evidence": "目标在前帧为人形，后帧显露长龙真身腾空"}
    with patch('gameplan.skills.grounded_casts.read_scene', return_value=earlier), patch('gameplan.ai.integrations.post_json',
        return_value={"done_reason": "stop", "message": {"content": json.dumps(candidate)}}) as post:
        result = detect(req, after, scene, bindings=IDENTITIES, enemies=["敖隐"], known_heroes=KNOWN_HEROES)
    assert bool(result["enemy_skill_events"]) == accepted
    assert bool(result["activity"]) == active
    assert "format" not in post.call_args.args[1]
    if accepted:
        assert result["enemy_skill_events"][0]["hero"] == "敖隐"
        assert result["enemy_skill_events"][0]["frame_index"] == 2


def test_frozen_sequence_never_calls_model_or_claims_cast():
    pixels, scene = frame("before")
    b64 = base64.b64encode(pixels).decode()
    req = VisionRequest(image_base64=b64, captured_at=100, focus="skills",
                        recent_frames=[{"image_base64": b64, "captured_at": 99}])
    with patch('gameplan.ai.integrations.post_json', side_effect=AssertionError("Frozen frame called model")):
        result = detect(req, pixels, scene, bindings=IDENTITIES, enemies=["敖隐"], known_heroes=KNOWN_HEROES)
    assert result["enemy_skill_events"] == []


def test_new_cast_cannot_be_inferred_from_identity_first_seen_afterwards():
    before, earlier = frame("before"); after, scene = frame("after")
    scene["targets"] = earlier["targets"]
    earlier["targets"] = []
    req = VisionRequest(image_base64=base64.b64encode(after).decode(), captured_at=100, focus="skills",
        recent_frames=[{"image_base64": base64.b64encode(before).decode(), "captured_at": 99}])
    candidate = {"state": "cast_start", "frame_index": 1, "confidence": 1, "evidence": "看到化龙"}
    with patch('gameplan.skills.grounded_casts.read_scene', return_value=earlier), patch('gameplan.ai.integrations.post_json',
        return_value={"done_reason": "stop", "message": {"content": json.dumps(candidate)}}):
        result = detect(req, after, scene, bindings=IDENTITIES, enemies=["敖隐"], known_heroes=KNOWN_HEROES)
    assert result["enemy_skill_events"] == []


def test_timeout_on_one_check_keeps_valid_cast_and_advances_rotation():
    before, earlier = frame("before"); after, scene = frame("after")
    req = VisionRequest(image_base64=base64.b64encode(after).decode(), captured_at=100, focus="skills",
        recent_frames=[{"image_base64": base64.b64encode(before).decode(), "captured_at": 99}])
    candidate = {"state": "cast_start", "frame_index": 1, "confidence": .96, "evidence": "人形转为长龙腾空"}
    response = {"done_reason": "stop", "message": {"content": json.dumps(candidate)}}
    with patch('gameplan.skills.grounded_casts.read_scene', return_value=earlier), patch('gameplan.ai.integrations.post_json',
        side_effect=[TimeoutError("local model timed out"), response]) as post:
        result = detect(req, after, scene, bindings=IDENTITIES, enemies=["敖隐"], known_heroes=KNOWN_HEROES,
                        equipped={"敖隐": "闪现"}, after_check=("敖隐", "穷乎玄间"))
    assert "闪现" in post.call_args_list[0].args[1]["messages"][0]["content"]
    assert [e["skill"] for e in result["enemy_skill_events"]] == ["穷乎玄间"]
    assert result["status"] == "incomplete_answer"
    assert result["checks_completed"] == 1


def test_exhausted_scan_budget_does_not_wait_on_every_visible_skill():
    before, earlier = frame("before"); after, scene = frame("after")
    req = VisionRequest(image_base64=base64.b64encode(after).decode(), captured_at=100, focus="skills",
        recent_frames=[{"image_base64": base64.b64encode(before).decode(), "captured_at": 99}])
    response = {"done_reason": "stop", "message": {"content": '{"state":"none"}'}}
    with patch('gameplan.skills.grounded_casts.read_scene', return_value=earlier), patch('gameplan.ai.integrations.post_json', return_value=response) as post, \
         patch('gameplan.skills.grounded_casts.time.monotonic', side_effect=[0, 0, 20]):
        result = detect(req, after, scene, bindings=IDENTITIES, enemies=["敖隐"], known_heroes=KNOWN_HEROES,
                        equipped={"敖隐": "闪现"})
    assert post.call_count == 1
    assert result["status"] == "partial"
    assert result["checks_attempted"] == 1 < result["checks_total"]
    assert result["last_check"] == ("敖隐", "穷乎玄间")


def test_api_preprocessing_and_raw_replay_use_identical_model_pixels():
    from gameplan.ai.integrations import image_bytes
    before, earlier = frame("before"); after, scene = frame("after")
    req = VisionRequest(image_base64=base64.b64encode(after).decode(), captured_at=100, focus="skills",
        recent_frames=[{"image_base64": base64.b64encode(before).decode(), "captured_at": 99}])
    _, prepared = image_bytes(req)
    response = {"done_reason": "stop", "message": {"content": '{"state":"none"}'}}
    with patch('gameplan.skills.grounded_casts.read_scene', return_value=earlier), patch('gameplan.ai.integrations.post_json', return_value=response) as post:
        for pixels in (after, prepared):
            detect(req, pixels, scene, bindings=IDENTITIES, enemies=["敖隐"], known_heroes=KNOWN_HEROES)
    assert post.call_args_list[0].args[1]["messages"] == post.call_args_list[1].args[1]["messages"]


def test_gameplay_hud_survives_clock_obscured_by_combat_text():
    root=FIXTURES/'combat'/'clock-occluded'
    readings=json.loads((root/'270-ocr.json').read_text(encoding='utf-8'))
    result=read_scene((root/'270.png').read_bytes(),KNOWN_HEROES,readings=readings)
    assert result is not None and not result['panel']


def test_fps_text_without_bottom_gameplay_controls_is_not_gameplay():
    root=FIXTURES/'combat'/'clock-occluded'
    readings=json.loads((root/'270-ocr.json').read_text(encoding='utf-8'))
    readings=[i for i in readings if i['text'] not in ('回城','恢复','闪现')]
    assert read_scene((root/'270.png').read_bytes(),KNOWN_HEROES,readings=readings) is None


def test_stream_scoreboard_binds_compact_columns_and_recovers_low_score_hero():
    root=FIXTURES/'combat'/'stream-scoreboard'
    readings=json.loads((root/'ocr.json').read_text(encoding='utf-8'))
    result=read_scene((root/'panel.png').read_bytes(),KNOWN_HEROES,readings=readings)
    assert result['enemy_roster']==['白起','西施','大乔','盘古','苍']
    assert result['ally_roster']==['妲己','狄仁杰','刘邦','程咬金','赵云']
    assert len(result['bindings'])==10
    assert next(b['nickname'] for b in result['bindings'] if b['hero']=='白起')=='巅峰召唤师9'


def test_partial_scoreboard_does_not_discard_two_readable_heroes():
    root=FIXTURES/'combat'/'stream-scoreboard'
    readings=json.loads((root/'ocr.json').read_text(encoding='utf-8'))
    readings=[i for i in readings if i['text'] not in KNOWN_HEROES or i['text'] in ('白起','西施')]
    result=read_scene((root/'panel.png').read_bytes(),KNOWN_HEROES,readings=readings)
    assert result['panel'] and result['enemy_roster']==['白起','西施']
    assert result['ally_roster']==[]
