# NovelAI for Krita

A Krita plugin for generating images with [NovelAI](https://novelai.net) from a
docker panel, similar to how [Krita AI Diffusion](https://github.com/Acly/krita-ai-diffusion)
works with local models.

- **Generate**: text to image at the canvas's aspect ratio.
- **Refine**: image to image on the whole canvas or a selection.
- **Inpaint**: regenerate a selection, using the surrounding canvas as context.
- **Account at a glance**: your plan, Anlas and remaining V5 Opus allowance, refreshed after
  each generation.
- **Anlas warning**: the panel says when the next generation would cost Anlas (over Opus's free
  size, steps or images per request, V5 allowance used up, or a plan without free generations).
- **Live preview**: watch the image form on the canvas while it generates (V4 and newer;
  can be turned off in Settings).
- **Quality tags and Undesired Content presets** for every model, exactly as the NovelAI site
  adds them.
- **Tag autocomplete** in both prompt boxes. NovelAI suggests tags as you type
  (Tab/Enter to insert), and typing Japanese suggests the matching English tags.
- Results go onto new layers and into a history list. Right-click a result to save
  NovelAI's original PNG (prompt info included), reuse its seed or prompt, or remove it.
  The history lives in memory, keeps the newest 50 results by default (Settings > History size)
  and clears when Krita closes; right-click > Clear history empties it.

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
| yes | < 100% | Inpaint the selection at that strength (V3 models: image to image) |

When the canvas is sent (refining or inpainting), it goes at its own resolution, like the
NovelAI site: for a selection, the area around it is widened up to the max generation size
from Settings (or the canvas edge) instead of being scaled up, which would blur the result.
Only areas larger than that size are scaled down. Text to image fills the max size and the
result is scaled to the canvas.
Inpainted layers keep exactly the pixels NovelAI changed, including the few pixels past the
selection where it blends the new content in, so edges match the NovelAI site.

## Development

```
plugin/
  novelai_krita.desktop     Krita plugin descriptor
  novelai_krita/
    api.py                  Request payloads and response parsing (plain Python, tested)
    client.py               Async HTTP via QNetworkAccessManager
    canvas.py               Reading the canvas/selection, inserting layers
    docker.py               The docker UI
    prompt_edit.py          Prompt box with tag autocomplete popup
    tags.py                 Finding the tag at the cursor (plain Python, tested)
    presets.py              Quality tags and Undesired Content presets (plain Python, tested)
    settings.py             Settings stored in kritarc
    settings_dialog.py      Token and size settings
    qt.py                   PyQt5 (Krita 5) / PyQt6 (Krita 6) import shim
scripts/
  install_dev.py            Link the plugin into Krita's pykrita folder
  package.py                Build the installable zip
tests/                      pytest tests for the plain-Python modules
```

To develop it against your own Krita, run this once and restart Krita after each change:

```
python scripts/install_dev.py
```

Run the tests with `python -m pytest`.

API reference: https://image.novelai.net/docs/index.html. The spec doesn't list model names,
quality tags or Undesired Content presets. Model names in `api.py` come from the NovelAI web
app (the model box accepts any name you type). Quality tags and presets in `presets.py` are
copied from NovelAI's docs ([quality tags](https://docs.novelai.net/en/image/qualitytags),
[undesired content](https://docs.novelai.net/en/image/undesiredcontent)) and combined the way
the site does it.

## Roadmap

- Multi-character prompts (V4 character captions and positions)
- Vibe transfer and precise reference images
- Director tools (`/ai/augment-image`) and upscaling (`/ai/upscale`)
