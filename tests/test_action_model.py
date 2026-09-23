import numpy as np

from gameplan.skills.action_model import LABELS, _prepare, build_network, load, review_sequence


def test_action_model_preprocess_has_temporal_shape():
    frames = [np.zeros((72, 128, 3), dtype=np.uint8) for _ in range(3)]
    value = _prepare(frames)
    assert tuple(value.shape) == (1, 1, 8, 64, 112)


def test_action_model_is_opt_in_without_checkpoint():
    frames = [np.zeros((72, 128, 3), dtype=np.uint8) for _ in range(2)]
    result = review_sequence(None, frames)
    assert result["enabled"] is False
    assert tuple(result["probabilities"]) == LABELS


def test_rejected_bootstrap_checkpoint_cannot_be_enabled(tmp_path):
    import torch
    path = tmp_path / "rejected.pt"
    torch.save({"state_dict": build_network().state_dict(),
                "deployment_eligible": False, "bootstrap_only": True}, path)
    assert load(path, device="cpu") is None
