import json

from novelai_krita import doc_state

STATE = {
    "prompt": "1girl, 銀髪, night sky", "negative_prompt": "lowres", "model": "nai-diffusion-5-full",
    "quality_tags": "light", "uc_preset": "Human Focus", "strength": 70, "steps": 28,
    "scale": 5.5, "cfg_rescale": 0.0, "sampler": "k_euler_ancestral",
    "noise_schedule": "karras", "seed": "1234", "random_seed": False, "n_samples": 1,
}


def test_round_trip():
    data = doc_state.encode(STATE)
    assert json.loads(data.decode("utf-8"))["version"] == doc_state.VERSION
    assert doc_state.decode(data) == STATE


def test_unknown_keys_are_dropped():
    data = doc_state.encode(dict(STATE, token="pst-secret", other=1))
    assert "token" not in data.decode("utf-8")
    assert doc_state.decode(json.dumps({"x": 1, "steps": 20}).encode()) == {"steps": 20}


def test_wrong_types_are_ignored():
    bad = {"prompt": 5, "steps": "28", "strength": True, "random_seed": 1,
           "scale": 6, "seed": 99, "n_samples": 2.5}
    assert doc_state.decode(json.dumps(bad).encode()) == {"scale": 6.0}


def test_unreadable_data_gives_nothing():
    for data in (b"", b"not json", b"[1, 2]", b'"text"', b"\xff\xfe", b"null"):
        assert doc_state.decode(data) == {}, data
