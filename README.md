# NovelAI for Krita

A Krita plugin for generating images with [NovelAI](https://novelai.net) from a
docker panel, similar to how [Krita AI Diffusion](https://github.com/Acly/krita-ai-diffusion)
works with local models.

- **Generate**: text to image at the canvas's aspect ratio.
- **Refine**: image to image on the whole canvas or a selection.
- **Inpaint**: regenerate a selection, using the surrounding canvas as context.
- Results go onto new layers and into a history list. Right-click a result to save
  NovelAI's original PNG (prompt info included), reuse its seed or prompt, or remove it.
  The history lives in memory and clears when Krita closes.

It requires a NovelAI subscription. Generations up to 1024 × 1024 pixels in area,
with 28 steps or fewer, are free on the Opus tier. Anything larger costs Anlas.

## Install

1. Run `python scripts/package.py` to build `dist/novelai_krita.zip`. Releases will also ship this zip.
2. In Krita: **Tools → Scripts → Import Python Plugin from File…**, pick the zip.
3. Restart Krita and make sure **NovelAI Image Generation** is enabled in
   **Settings → Configure Krita → Python Plugin Manager**.
4. Show the panel with **Settings → Dockers → NovelAI**.
5. Click **Settings** in the panel and paste a **Persistent API Token**
   (novelai.net → User Settings → Account → Get Persistent API Token). Use **Test** to check it.

The token is stored in plain text in Krita's settings file (`kritarc`) on your computer.

## Using it

The Strength slider and the current selection decide what Generate does:

| Selection | Strength | Result |
|-----------|----------|--------|
| none | 100% | New image for the whole canvas |
| none | < 100% | Image to image on the whole canvas |
| yes | 100% | Inpaint the selection |
| yes | < 100% | Image to image on the selection only |

The region is scaled to fit the max generation size from Settings, in multiples of 64. The result is scaled back afterwards.
Small selections are generated at full resolution and scaled down, which keeps detail.
A feathered selection blends the result softly into the canvas.

## Development

```
plugin/
  novelai_krita.desktop     Krita plugin descriptor
  novelai_krita/
    api.py                  Request payloads and response parsing (plain Python, tested)
    client.py               Async HTTP via QNetworkAccessManager
    canvas.py               Reading the canvas/selection, inserting layers
    docker.py               The docker UI
    settings.py             Settings stored in kritarc
    settings_dialog.py      Token and size settings
    qt.py                   PyQt5 (Krita 5) / PyQt6 (Krita 6) import shim
scripts/
  install_dev.py            Link the plugin into Krita's pykrita folder
  package.py                Build the installable zip
tests/                      pytest tests for api.py
```

To develop it against your own Krita, run this once and restart Krita after each change:

```
python scripts/install_dev.py
```

Run the tests with `python -m pytest`.

API reference: https://image.novelai.net/docs/index.html. The spec doesn't list model names
or quality tags. The values in `api.py` are taken from the NovelAI web app, and the model
box accepts any model name you type.

## Roadmap

- Live preview using the streaming endpoint (`/ai/generate-image-stream`)
- Multi-character prompts (V4 character captions and positions)
- Vibe transfer and precise reference images
- Director tools (`/ai/augment-image`) and upscaling (`/ai/upscale`)
- Tag autocomplete (`/ai/generate-image/suggest-tags`)
- Anlas cost estimate before generating
