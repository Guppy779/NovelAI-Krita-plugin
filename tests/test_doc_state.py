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


def test_characters_round_trip():
    characters = [{"prompt": "girl, red hair", "negative": "blue hair", "x": 0.1, "y": 0.9,
                   "enabled": True},
                  {"prompt": "boy", "negative": "", "x": 0.5, "y": 0.5, "enabled": False}]
    state = dict(STATE, characters=characters, character_positions=True)
    assert doc_state.decode(doc_state.encode(state)) == state


def test_damaged_characters_are_repaired_or_dropped():
    stored = {"characters": [
        {"prompt": "girl"},  # Missing fields: defaults.
        {"prompt": "boy", "negative": 3, "x": 1.5, "y": True, "enabled": "yes"},
        {"negative": "no prompt"}, "text", None,
    ], "character_positions": "on"}
    assert doc_state.decode(json.dumps(stored).encode()) == {"characters": [
        {"prompt": "girl", "negative": "", "x": 0.5, "y": 0.5, "enabled": True},
        {"prompt": "boy", "negative": "", "x": 0.5, "y": 0.5, "enabled": True},
    ]}
    assert doc_state.decode(json.dumps({"characters": "girl"}).encode()) == {}


def test_characters_are_not_remembered_for_new_documents():
    assert "characters" in doc_state.PROMPTS


def test_precise_references_stay_out_of_the_document():
    # On purpose: their pictures would add megabytes to every .kra.
    references = [{"image": "0123abcd", "kind": "character", "strength": 0.8}]
    data = doc_state.encode(dict(STATE, references=references))
    assert b"references" not in data and b"0123abcd" not in data
    stored = json.dumps({"steps": 20, "references": references}).encode()
    assert doc_state.decode(stored) == {"steps": 20}
