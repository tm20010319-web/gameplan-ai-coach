"""Weak labels must preserve attribution and uncertainty before training."""
from scripts.pseudo_label_action_candidates import accept_event, sample_frames
from scripts.train_ultimate_action_model import read_clip
import cv2
import numpy as np
import pytest


def sequence():
    return [{'timestamp_s': i*.6, 'targets':[{'hero':'敖隐'}]} for i in range(6)]


def proposal(**changes):
    return {'hero':'敖隐','label':'cast_start','frame_index':2,
            'confidence':.95,'evidence':'目标由人形化为长龙腾空', **changes}


@pytest.mark.parametrize('change', [
    {'hero':'艾琳'}, {'confidence':float('nan')}, {'confidence':1.01},
    {'frame_index':0}, {'frame_index':6}, {'frame_index':True}, {'evidence':''},
])
def test_invalid_or_unattributed_positive_stays_unknown(change):
    assert accept_event(proposal(**change),sequence(),['敖隐'])[0]=='unknown'


def test_lost_identity_cannot_become_cast_start():
    frames=sequence()
    frames[1]['targets']=[]
    assert accept_event(proposal(),frames,['敖隐'])==('unknown','onset_identity_unconfirmed')


def test_valid_automatic_agreement_is_still_only_weak_supervision():
    assert accept_event(proposal(),sequence(),['敖隐'])==('cast_start','weak_label_only')


def test_end_of_video_is_clamped_and_training_preserves_clip_axis(tmp_path):
    path=tmp_path/'short.avi'
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),10,(128,72))
    assert writer.isOpened()
    for i in range(20):writer.write(np.full((72,128,3),i*10,dtype=np.uint8))
    writer.release()
    item={'video':str(path),'start_s':1.,'end_s':2.8}
    frames=sample_frames(item)
    assert len(frames)==6
    assert frames[-1]['timestamp_s']<2.
    item['end_s']=frames[-1]['timestamp_s']
    import torch
    tensor=torch.stack([read_clip(item),read_clip(item)])
    assert tensor.shape==(2,1,8,64,112)
    from gameplan.skills.action_model import build_network
    with torch.inference_mode():
        assert build_network().eval()(tensor).shape==(2,3)
