# NovelAI for Krita

Generate, refine and inpaint images with [NovelAI](https://novelai.net) without leaving Krita.
The plugin adds a panel (a docker), in the spirit of
[Krita AI Diffusion](https://github.com/Acly/krita-ai-diffusion), that sends your prompt, and
when needed your canvas and selection, to NovelAI. Each result arrives as a new layer.

This is an unofficial, community-made plugin. It isn't affiliated with or endorsed by NovelAI
or Anlatan.

## Features

- **Text to image** at your canvas's aspect ratio, with all current NovelAI models, V5 included.
- **Refine** the whole canvas or a selection with image to image.
- **Inpaint** a selection the same way the NovelAI site does, at full resolution and blended
  into the surrounding canvas.
- **Live preview** that shows the image forming on the canvas while it generates.
- **Tag autocomplete** in both prompt boxes. Typing Japanese also suggests the matching
  English tags.
- **Quality tags and Undesired Content presets** for each model, applied the way the site
  applies them.
- **Your account at a glance**: your plan, Anlas and remaining V5 allowance.
- **A warning before you spend Anlas**, with the reason it would cost.
- **A history of results**, from which you can re-add, save, or reuse a result's seed or prompt.

## Requirements

- Krita 5.2 or newer. It has been tested on Krita 5.2.5 on Windows.
- A NovelAI subscription with image generation. On **Opus**, generations up to
  1024 × 1024 pixels in area, at 28 steps or fewer and one image at a time, are free. Larger
  or extra work, and every generation on other plans, costs Anlas.
- An internet connection. Generation runs on NovelAI's servers, so no GPU is needed.

## Install

1. Download `novelai_krita.zip` from the
   [Releases page](https://github.com/Guppy779/NovelAI-Krita-plugin/releases). Don't unzip it.
2. In Krita, open **Tools → Scripts → Import Python Plugin from File…** and choose the zip.
   If Krita asks whether to activate the plugin, choose **Yes**.
3. Restart Krita.
4. Open the panel with **Settings → Dockers → NovelAI**.

If the panel isn't listed, go to **Settings → Configure Krita → Python Plugin Manager**,
tick **NovelAI Image Generation**, and restart Krita again.

### Connect your NovelAI account

1. On [novelai.net](https://novelai.net), open **User Settings → Account → Get Persistent API
   Token** and copy the token. It starts with `pst-`.
2. In the NovelAI panel, click **Settings**, paste the token, and click **Test**. The test
   shows your plan and Anlas.
3. Click **OK**. The line under the model box now shows your account, for example
   *Opus · 15,441 Anlas · V5 99%*.

### Update or uninstall

- **Update:** import the new zip the same way, then restart Krita. Your settings are kept.
- **Uninstall:** untick the plugin in the Python Plugin Manager. To remove it completely,
  delete `novelai_krita` and `novelai_krita.desktop` from Krita's `pykrita` folder.
  **Settings → Manage Resources → Open Resource Folder** opens the folder that contains
  `pykrita`. On Windows that is `%APPDATA%\krita\pykrita`.

## Using it

1. Open or create a document. Its aspect ratio sets the shape of the image.
2. Pick a model, write a prompt (tags such as `1girl, silver hair, night sky`), and optionally
   add undesired content.
3. Click **Generate**. The result is added as a new layer above the active layer.

### What Generate does

What Generate does depends on two things: the **Strength** slider, and whether part of the
image is selected. The line under the slider always says which mode is active.

| Selection | Strength | What happens |
|-----------|----------|--------------|
| none | 100% | A new image for the whole canvas |
| none | below 100% | The whole canvas is refined (image to image) |
| yes | 100% | The selection is inpainted, using the area around it as context |
| yes | below 100% | The selection is inpainted at that strength, so more of the original survives (V3 models use image to image instead) |

Lower strength keeps more of what's already there.

### Inpainting

Select the area to change with any selection tool, set Strength, and click Generate.

- The plugin sends the selection along with some of the canvas around it. **Settings →
  Selection context** controls how much. More context helps the new content blend in.
- The canvas is sent at its own resolution, like on the NovelAI site. A small selection is
  not scaled up, because that would blur the result.
- The new layer contains only the pixels NovelAI changed. That can include a few pixels past
  the selection, where NovelAI blends the edges. To get NovelAI's whole image as the layer
  instead, turn on **Settings → Inpainting: Add the whole image as the layer**.

### Prompt tools

- **Quality tags** and **Undesired preset** work like the toggles under the prompt box on
  the NovelAI site. They are added to the request but not shown in the text boxes. As on the
  site, models that aren't Curated also get `nsfw` in the undesired content, unless your
  prompt contains it.
- **Autocomplete** suggests tags for the word at the cursor, free of charge. Use Up and Down
  to choose, **Tab** or **Enter** to insert, and **Escape** to close the list. Italic
  suggestions are fuzzy matches.
- **Advanced options** has steps, prompt guidance, guidance rescale, sampler, noise schedule,
  seed and the number of images. Untick **Random** to keep a fixed seed.

### Anlas

The account line updates after every generation. Click **Refresh** to check it again, or
hover over it for details such as when the V5 allowance next refills.

When the next generation would cost Anlas, an orange **Costs Anlas** note appears under the
mode text and says why. Possible reasons:

- The image is over 1024 × 1024 in area.
- Steps are above 28.
- More than one image is requested.
- The V5 allowance is used up.
- Your plan has no free generations.

To keep generating for free on Opus, keep **Settings → Max generation size** at 1024.

### History

Each result is also kept in the history at the bottom of the panel.

- **Double-click** a result to add it as a layer again.
- **Right-click** a result to:
  - save it to disk, as the original PNG NovelAI sent, with the prompt and settings embedded
  - reuse its seed or prompt
  - remove it from the history
- **Right-click → Clear history** empties the history. Layers you already added stay.

The history is kept in memory only, so it is cleared when Krita closes. Save anything you
want to keep.

## Settings

Click **Settings** in the panel:

| Setting | Default | What it does |
|---------|---------|--------------|
| API token | — | Your NovelAI Persistent API Token. **Test** checks it. |
| Max generation size | 1024 px | Generations are sized to about this many pixels squared. Above 1024 costs Anlas. |
| Selection context | 20% | How much of the canvas around a selection is sent, relative to the selection's size. |
| Autocomplete | on | Suggest tags while you type. |
| Inpainting: Add the whole image as the layer | off | Use NovelAI's whole image as the layer instead of only the changed pixels. |
| Live preview | on | Show each step on the canvas in a temporary *NovelAI preview* layer while generating (V4 and newer). |
| History size | 50 | How many results the history keeps. The oldest are dropped first. |

## Privacy

- The API token is stored in plain text in Krita's settings file (`kritarc`) on your
  computer. It is only sent to NovelAI.
- The plugin talks only to `image.novelai.net`. It sends your prompts, and when you refine or
  inpaint, the part of the canvas being worked on.
- The plugin does not save results to disk on its own. Only **Save image…** writes a file.

## Troubleshooting

- **The NovelAI docker is missing:** enable the plugin in the Python Plugin Manager and
  restart Krita. If the plugin is greyed out there, hover over it to see the error, and
  include that error when you report the problem.
- **"Your API token is missing, invalid or expired":** make a new Persistent API Token on
  novelai.net and paste it into Settings.
- **"Another generation is still running on your account":** NovelAI runs one generation at
  a time per account, including generations started on the website. Wait for the other one
  to finish.
- **"Not enough Anlas":** the generation is over Opus's free limits (see the Costs Anlas
  note) and your balance can't cover it.
- **The inpainted area doesn't blend in:** raise **Selection context** so NovelAI sees more
  of the surrounding image.

Bug reports and ideas are welcome in
[Issues](https://github.com/Guppy779/NovelAI-Krita-plugin/issues).

## Roadmap

- Multi-character prompts (V4 character captions and positions)
- Vibe transfer and precise reference, once they work with V5
- Director tools and upscaling

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
    settings_dialog.py      The Settings dialog
    Manual.html             Help shown in Krita's Python Plugin Manager
    qt.py                   PyQt5 (Krita 5) / PyQt6 (Krita 6) import shim
scripts/
  install_dev.py            Link the plugin into Krita's pykrita folder (--remove to undo)
  package.py                Build dist/novelai_krita.zip
tests/                      pytest tests for the plain-Python modules
```

To work on the plugin against your own Krita, link it once, then restart Krita after each
change:

```
python scripts/install_dev.py
```

- **Tests:** `python -m pytest`
- **Build the release zip:** `python scripts/package.py`

API reference: https://image.novelai.net/docs/index.html. The spec doesn't list model names,
quality tags or Undesired Content presets:

- Model names in `api.py` come from the NovelAI web app. You can also type any model name
  into the model box.
- Quality tags and presets in `presets.py` are copied from NovelAI's docs
  ([quality tags](https://docs.novelai.net/en/image/qualitytags),
  [undesired content](https://docs.novelai.net/en/image/undesiredcontent)) and combined the
  way the site combines them.
