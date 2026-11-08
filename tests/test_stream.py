import base64
import json

from novelai_krita import api

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"
PNG = b"\x89PNG\r\n\x1a\nfake-png"


def block(event, **fields):
    data = json.dumps(dict(event_type=event, **fields))
    return "event: %s\ndata: %s\n\n" % (event, data)


# Same shape as a real stream captured from image.novelai.net.
STREAM = (
    block("intermediate", samp_ix=0, step_ix=0, gen_id=7960023, sigma=20.3,
          image=base64.b64encode(JPEG).decode())
    + block("intermediate", samp_ix=0, step_ix=1, gen_id=7960023, sigma=14.2,
            image=base64.b64encode(JPEG).decode())
    + block("final", samp_ix=0, gen_id=7960023, image=base64.b64encode(PNG).decode())
).encode()


def test_parses_real_event_shapes():
    events = api.SSEParser().feed(STREAM)
    assert [(e.kind, e.sample, e.step) for e in events] == [
        ("intermediate", 0, 0), ("intermediate", 0, 1), ("final", 0, 0)]
    assert events[0].image == JPEG and events[2].image == PNG


def test_chunks_split_anywhere():
    for size in (1, 7, 64, 1000):
        parser = api.SSEParser()
        events = []
        for i in range(0, len(STREAM), size):
            events += parser.feed(STREAM[i:i + size])
        events += parser.finish()
        assert [e.kind for e in events] == ["intermediate", "intermediate", "final"], size
        assert events[-1].image == PNG


def test_crlf_comments_and_missing_trailing_blank_line():
    text = (": keep-alive\r\n\r\n" + block("final", samp_ix=1, image="")).replace("\n", "\r\n")
    parser = api.SSEParser()
    events = parser.feed(text.encode()[:-4])  # Stream ends without the final blank line.
    events += parser.finish()
    assert [(e.kind, e.sample) for e in events] == [("final", 1)]


def test_error_and_retry_events():
    data = (block("error", code=500, message="Worker crashed") + block("retry", message="again"))
    events = api.SSEParser().feed(data.encode())
    assert [(e.kind, e.message) for e in events] == [("error", "Worker crashed"), ("retry", "again")]


def test_garbage_is_ignored():
    events = api.SSEParser().feed(b"data: not json\n\nevent: x\n\n")
    assert events == []


def test_valid_json_of_the_wrong_shape_is_ignored():
    data = (b'data: [1, 2]\n\ndata: "text"\n\n'
            b'data: {"event_type": "final", "samp_ix": "x"}\n\n'
            b'data: {"event_type": "final", "image": "not base64!"}\n\n')
    assert api.SSEParser().feed(data) == []


def test_supports_streaming():
    assert api.supports_streaming("nai-diffusion-4-5-full")
    assert api.supports_streaming("nai-diffusion-5-full")
    assert not api.supports_streaming("nai-diffusion-3")
