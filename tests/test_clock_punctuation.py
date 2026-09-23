"""Real HUD OCR regression; this does not assert successful skill recognition."""
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from gameplan.skills.combat_evidence import clock_label, read_scene
from gameplan.ai.advisor import KNOWN_HEROES

ROOT=Path(__file__).parent/'fixtures/combat/clock-punctuation'

@pytest.mark.parametrize('text',['11:27.','11:27。',' 11：27. ','03:33'])
def test_clock_accepts_formatting_noise(text):
    assert clock_label(text)

@pytest.mark.parametrize('text',['FPS30 11:27','11:99','11:2','1127','12/27','abc11:27',''])
def test_clock_does_not_invent_time(text):
    assert not clock_label(text)

def test_real_clock_punctuation_keeps_gameplay_without_inventing_sunce_identity():
    readings=json.loads((ROOT/'ocr.json').read_text(encoding='utf8'))
    assert any(r['text']=='11:27.' for r in readings)
    bindings=[{'hero':'孙策','nickname':'无阙焕','side':'enemy_roster'}]
    result=read_scene((ROOT/'sunce-866.2.png').read_bytes(),KNOWN_HEROES,bindings,readings=readings)
    assert result is not None and not result['panel']
    assert not result['targets']  # Visible ship/status text cannot fabricate a nickname.
    assert not result['hero_levels']

def test_hud_clock_still_needs_gameplay_labels():
    readings=json.loads((ROOT/'ocr.json').read_text(encoding='utf8'))
    readings=[r for r in readings if r['text'] not in ('回城','恢复','闪现')]
    result=read_scene((ROOT/'sunce-866.2.png').read_bytes(),KNOWN_HEROES,[],readings=readings)
    assert result is None
