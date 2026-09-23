import json
import io
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from gameplan.ai.advisor import KNOWN_HEROES
from gameplan.vision.summoner_icons import templates, match_icon, read_equipment

FIXTURES = Path(__file__).parent / "fixtures" / "summoner"


def test_verified_partial_panel_reuses_recovered_hero_row_without_extra_ocr():
    # The full OCR merged hero/nickname and missed a tab; the scene reader
    # independently recovered the official hero label in its own crop.
    pixels = np.full((576, 1280, 3), 20, dtype=np.uint8)
    icon = next(original for skill, original in templates() if skill == '闪现')
    pixels[332:360, 698:726] = cv2.resize(icon, (28, 28), interpolation=cv2.INTER_AREA)
    buffer = io.BytesIO()
    Image.fromarray(cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)).save(buffer, 'PNG')
    anchor = {'text': '铠', 'score': .95, 'box': [[700, 300], [724, 300], [724, 320], [700, 320]]}
    scene = {'panel': True, 'hero_rows': [anchor]}
    with patch('gameplan.vision.hero_recognition.read_text', side_effect=AssertionError('redundant OCR')):
        found = read_equipment(buffer.getvalue(), KNOWN_HEROES, readings=[], panel_scene=scene)
        assert [(item['hero'], item['skill']) for item in found] == [('铠', '闪现')]
        assert read_equipment(buffer.getvalue(), KNOWN_HEROES, readings=[],
                              panel_scene={**scene, 'panel': False}) == []


def test_real_partial_tab_scoreboard_shares_its_validated_rows_with_equipment_reader():
    from gameplan.skills.combat_evidence import read_scene
    root = FIXTURES.parent / 'combat' / 'scoreboard-partial-tabs'
    pixels = (root / 'panel.png').read_bytes()
    readings = json.loads((root / 'ocr.json').read_text(encoding='utf8'))
    with patch('gameplan.skills.combat_evidence.read_text', return_value=[]):
        scene = read_scene(pixels, KNOWN_HEROES, readings=readings)
    assert scene['panel'] and scene['enemy_roster']
    with patch('gameplan.vision.summoner_icons.match_icon', return_value={'skill': '闪现', 'score': .93}):
        assert read_equipment(pixels, KNOWN_HEROES, readings=readings) == []
        found = read_equipment(pixels, KNOWN_HEROES, readings=readings, panel_scene=scene)
    assert {item['hero'] for item in found} == set(scene['ally_roster'] + scene['enemy_roster'])


def test_official_icons_are_distinguished_and_blank_or_unrelated_pixels_stay_unknown():
    for skill, original in templates():
        # Place an official icon at scoreboard scale, with surrounding UI pixels.
        image = np.full((40, 41, 3), 20, dtype=np.uint8)
        image[7:35, 6:34] = cv2.resize(original, (28, 28), interpolation=cv2.INTER_AREA)
        expected = {"晕眩": "眩晕"}.get(skill, skill)
        assert match_icon(image)["skill"] == expected
    assert match_icon(np.zeros((40, 41, 3), dtype=np.uint8)) is None
    assert match_icon(np.random.default_rng(2).integers(0, 255, (40, 41, 3), dtype=np.uint8)) is None


def test_game_hud_and_skill_labels_cannot_be_mistaken_for_scoreboard_equipment():
    path = FIXTURES / "scoreboard.jpg"
    pixels = path.read_bytes()
    # A Flash label alone must not assign Flash to any enemy.
    readings = [{"text": "闪现", "score": 1, "box": [[10, 10], [30, 10], [30, 20], [10, 20]]}]
    assert read_equipment(pixels, KNOWN_HEROES, readings=readings) == []


def test_disputed_scoreboard_matches_are_unknown_instead_of_promoted_to_certainty():
    root = FIXTURES
    readings = json.loads((root / "scoreboard-ocr.json").read_text(encoding="utf-8"))
    pixels = (FIXTURES / "scoreboard.jpg").read_bytes()
    results = {r["hero"]: r["skill"] for r in read_equipment(pixels, KNOWN_HEROES, readings=readings)}
    # User disputed the former labels. Similarity near .81 is not a confirmed
    # positive example and must not be rewritten as confidence 1.0.
    assert "吕布" not in results
    assert "瑶" not in results
    assert results.get("孙策") != "闪现"
    assert results.get("云缨") != "闪现"


def test_template_score_is_preserved_instead_of_rewritten_as_full_confidence():
    readings = json.loads((FIXTURES / "scoreboard-ocr.json").read_text(encoding="utf-8"))
    with patch('gameplan.vision.summoner_icons.match_icon', return_value={"skill": "闪现", "score": .93}):
        results = read_equipment((FIXTURES / "scoreboard.jpg").read_bytes(), KNOWN_HEROES, readings=readings)
    assert results
    assert all(item["confidence"] == .93 and "非准确率" in item["evidence"] for item in results)
