# NovelAI for Krita

Generate, refine and inpaint images with [NovelAI](https://novelai.net) without leaving Krita.
The plugin adds a panel (a docker), in the spirit of
[Krita AI Diffusion](https://github.com/Acly/krita-ai-diffusion), that sends your prompt, and
when needed your canvas, to NovelAI. Each result opens in a preview window, where you add the ones you like as layers.

This is an unofficial, community-made plugin. It isn't affiliated with or endorsed by NovelAI
or Anlatan.

## Features

- **Text to image** at your canvas's aspect ratio, with all current NovelAI models, V5 included.
- **Refine** the whole canvas with image to image.
- **Inpaint** by painting a mask over your canvas, the same way the NovelAI site does,
  including **focused inpainting** for more detail in part of the image.
- **A preview window** that shows the image forming while it generates, with **Generate**
  and **Add as layer** buttons, so you can keep trying until you get one you like.
- **Tag autocomplete** in both prompt boxes. Typing Japanese also suggests the matching
  English tags.
- **Coloured prompt weights**, like the site: `{ }`, `[ ]` and `1.2::tag::` show at a glance.
- **Quality tags and Undesired Content presets** for each model, applied the way the site
  applies them.
- **Character prompts** (V4 and newer): a prompt and undesired content for each character,
  placed where the AI chooses or at positions you pick.
- **Precise reference** (V4.5): give NovelAI a picture to take a character, a style or both
  from, with the site's Strength and Fidelity sliders.
- **Import from an image**: drop a NovelAI image on a prompt box to reuse its prompt,
  undesired content, characters, settings or seed.
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
   *Opus · 12,500 Anlas · V5 85%*.

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
3. Click **Generate**. The result appears in the preview window. Click **Add as layer** to
   put it above the active layer, or **Generate** again for another one.

### Settings per document

Each document keeps its own prompt, undesired content, characters and generation settings
(model, presets, strength, steps, sampler, seed and so on). Switching to another document switches
the panel to that document's settings.

- They're saved inside the `.kra` file, so reopening it brings them back. Saving as PNG or
  another flat format doesn't keep them.
- A new document, or an image without saved settings, starts with the settings of your last
  generation and empty prompts.
- Changing only the prompt doesn't mark the document as modified, so save it yourself if
  you want to keep a prompt you haven't generated with yet.
- Precise references aren't saved in the `.kra`. Each open document has its own, but only
  until you close it.

### What Generate does

What Generate does depends on two things: the **Strength** slider, and whether you've set an
inpainting mask. The line under the slider always says which mode is active.

| Mask | Strength | What happens |
|------|----------|--------------|
| none | 100% | A new image for the whole canvas |
| none | below 100% | The whole canvas is refined (image to image) |
| set | 100% | The masked area is regenerated |
| set | below 100% | The masked area is regenerated at that strength, so more of the original survives (V4 and newer) |

Lower strength keeps more of what's already there.

### Inpainting

Click **Inpaint...** under the Strength slider. A window opens with a picture of your canvas.
Scroll to zoom, and drag with the middle or right mouse button to move around.

- **Brush** and **Eraser** paint the area to regenerate, in NovelAI's 8 x 8 pixel cells. If you
  had something selected in Krita, it is already painted in.
- **Focus** works on part of the canvas at a higher resolution. Drag a rectangle (up to 768 x
  768 pixels of area) around, say, a face. It is enlarged to about one megapixel for
  inpainting, so it gets more detail, and is free on Opus even on a big canvas. Paint inside it,
  or leave it empty to regenerate all of it. **Minimum context** is the red border inside the
  rectangle: NovelAI sees it, but only the part inside it changes. Drag the rectangle to move it;
  the red border can hang off the edge of the canvas, so you can inpaint right up to the edge.

Click **Use mask**. The button changes to **Edit mask...**, a small picture of the mask appears
next to it (click it to edit the mask too), and every **Generate** now inpaints that area, so you can keep trying with a different seed. It works with what's on the canvas when
you click **Generate**. **Clear mask** goes back to generating the whole image. Masks last
until you clear them, switch document or change the canvas size, and aren't saved in the `.kra`.

- Without Focus, the whole canvas is sent at its own size, as on the NovelAI site. A canvas
  above **Max generation size** is scaled down to fit, and the result is scaled back up, which
  softens it. Use Focus to keep full detail.
- The new layer holds the new area, fading out just past its edges, the way the site blends it.
  To get NovelAI's whole image as the layer instead, turn on **Settings → Inpainting: Add the
  whole image as the layer**.

### Prompt tools

- **Quality tags** and **Undesired preset** work like the toggles under the prompt box on
  the NovelAI site. They are added to the request but not shown in the text boxes. As on the
  site, models that aren't Curated also get `nsfw` in the undesired content, unless your
  prompt contains it.
- **Autocomplete** suggests tags for the word at the cursor, free of charge. Use Up and Down
  to choose, **Tab** or **Enter** to insert, and **Escape** to close the list. Italic
  suggestions are fuzzy matches.
- **Weights are coloured** as you type, as on the site: orange-red for tags weighted above 1
  (`{tag}`, `1.2::tag::`), blue for below 1 (`[tag]`, `0.8::tag::`) and green for the closing
  `::`. The further a weight is from 1, the stronger the colour.
- **Advanced options** has steps, prompt guidance, guidance rescale, sampler, noise schedule,
  seed and the number of images. Untick **Random** to keep a fixed seed.

### Characters

With V4 and newer models, each character in the image can have its own prompt, so that
traits don't leak from one character to another. See NovelAI's guide to
[multiple characters](https://docs.novelai.net/en/image/multiplecharacters).

1. Put the scene, the style and the number of characters in the main prompt, for example
   `2girls, autumn park, sitting on bench`.
2. Click **+ Add** under the undesired content box for each character, and describe each
   one with `girl`, `boy` or `other` and no number, for example `girl, red hair, smile`.
3. If a trait leaks onto a character anyway, add it to that character's undesired content.

Each character has:

- **A checkbox:** untick it to leave the character out without deleting it.
- **Up and down arrows:** set the order. NovelAI tends to place characters in that order,
  left to right.
- **✕:** removes the character.

**Positions:** the box next to **+ Add** switches between **AI's choice**, where NovelAI
places the characters, and **Custom positions**. With custom positions, each character
gets a position button. Click it to open a small canvas in the shape of the image, with a
numbered dot for each character:

- Drag any dot to place that character's centre. Clicking an empty spot moves the
  highlighted character, the one whose button you clicked.
- **V5** places characters anywhere. The button shows the position as percentages from the
  left and top, for example **31%, 62%**.
- **V4 and V4.5** only accept a 5 × 5 grid, so the dots snap to its cells. The button shows
  the cell, for example **C3**: columns A to E from left to right, rows 1 to 5 from top to
  bottom. A position placed freely on V5 is kept, and V4 and V4.5 use its nearest cell.
- An unticked character's dot has a dashed outline.

V4 and V4.5 take up to 6 characters, V5 up to 22. V3 models have no character prompts.
If the model doesn't take the ticked characters, a note says why and Generate stops.

Characters are part of the document's settings and are saved in the `.kra`. A new
document starts without characters.

You can also use `source#`, `target#` and `mutual#` in front of an action tag for
interactions, for example `source#hug` in one character and `target#hug` in the other.

### Precise reference

A precise reference is a picture for NovelAI to take a character, a style or both from. It
works like **Precise Reference** on the NovelAI site, and only with **V4.5 Full** and **V4.5
Curated**. NovelAI doesn't offer it for V5 yet. With another model selected the references
stay in the panel, greyed out, and aren't sent.

Click **+ Add** next to **Precise Reference** and choose **From file...** or **From canvas**
(the whole visible canvas), or drop an image file on the section. Each reference has:

- **A checkbox:** untick it to leave the reference out without deleting it.
- **A type:**
  - **Character & Style** takes the character's looks and the art style.
  - **Character** takes only the character's looks.
  - **Style** takes only the art style.
- **Strength:** how strongly the reference is followed. Lower it if poses and expressions
  copy the reference too closely.
- **Fidelity:** how exactly the reference is applied. At 1 it is hard to change with the
  prompt, and lower values give the prompt more say.
- **✕:** removes the reference.

Both sliders run from 0 to 1. Type in the number box to go beyond that, below 0 included, as
on the site.

Things to know:

- **It always costs Anlas:** 5 per reference for each image, on top of the normal price, on
  Opus too. The **Costs Anlas** note shows the amount.
- **Size:** NovelAI takes references at 1024 × 1536, 1536 × 1024 or 1472 × 1472. Your picture is
  scaled to fit the nearest of those, with black around it where it doesn't fill it.
- **Several references:** you can add up to 16 and mix types, such as one character and one
  style. Several character references blend into one character.
- **Inpainting:** references are used when you inpaint as well.
- **One set for each document:** every open document has its own references, and switching
  to another document switches the panel to that document's references.
- **Not saved:** references last until you remove them, or close the document or Krita.
  They aren't stored in the `.kra`, which keeps your files small, so add them again after
  reopening a document.

NovelAI's [Precise Reference guide](https://docs.novelai.net/en/image/precisereference) has
tips on what makes a good reference picture.

### Import from an image

Drag an image made with NovelAI from your file browser onto either prompt box. A dialog
shows what the image contains. Tick what to import, then click **Import**:

| Part | What it sets |
|------|--------------|
| Prompt | The prompt box |
| Undesired content | The undesired content box |
| Characters | The character prompts, with their positions. They replace your characters, or with **Add to the current characters**, come after them. An image with custom positions turns on **Custom positions**. |
| Settings | The model, steps, prompt guidance, guidance rescale, sampler and noise schedule |
| Seed | The seed, with **Random** turned off |

The dialog remembers your choices.

NovelAI records the prompt as it was sent, with the quality tags at the end and the
Undesired Content preset at the start. **Move quality tags and preset into their boxes**
takes them out of the text and chooses them in the **Quality tags** and **Undesired
preset** boxes instead, if the selected model has them. If it doesn't, they stay in the
text so they're still sent. With the option off, the text is imported as recorded and
both boxes are turned off.

NovelAI stores the prompt in the PNG's text and again, hidden, in the image's
transparency. The hidden copy survives some programs that remove the text. JPEGs and
images re-saved by most websites have neither, so there's nothing to import from them.

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
- A precise reference is used (5 Anlas each per image, on every plan).

To keep generating for free on Opus, keep **Settings → Max generation size** at 1024.

### History

Each result is kept in the history at the bottom of the preview window. Click one to see it
large. The **Preview** button next to **Generate** reopens the window if you've closed it.

- **Double-click** a result to add it as a layer.
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
| Autocomplete | on | Suggest tags while you type. |
| Inpainting: Add the whole image as the layer | off | Use NovelAI's whole image as the layer instead of only the inpainted area. |
| Live preview | on | Show each step in the preview window while generating (V4 and newer). |
| History size | 50 | How many results the history keeps. The oldest are dropped first. |

## Privacy

- The API token is stored in plain text in Krita's settings file (`kritarc`) on your
  computer. It is only sent to NovelAI.
- Your prompts are stored only inside the documents you save as `.kra`, not in `kritarc`.
  Share a `.kra` file and its prompts go with it. `kritarc` keeps the other settings of
  your last generation, for new documents.
- The plugin talks only to `image.novelai.net`. It sends your prompts, and when you refine or
  inpaint, the part of the canvas being worked on. Precise reference pictures are sent with
  each generation that uses them, and aren't stored anywhere by the plugin.
- The plugin does not save results to disk on its own. Only **Save image…** writes a file.
- **Import from an image** reads the image on your computer. Nothing from it is sent anywhere.

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
- **Changes leak outside the area, or it doesn't blend in:** paint the mask a little past what
  you want changed. With Focus, raise **Minimum context** so NovelAI sees more around it.

Bug reports and ideas are welcome in
[Issues](https://github.com/Guppy779/NovelAI-Krita-plugin/issues).

## Roadmap

- Vibe transfer
- Precise reference for V5, once NovelAI supports it
- Director tools and upscaling

## Development

```
plugin/
  novelai_krita.desktop     Krita plugin descriptor
  novelai_krita/
    api.py                  Request payloads and response parsing (plain Python, tested)
    client.py               Async HTTP via QNetworkAccessManager
    canvas.py               Reading the canvas and selection, inserting layers
    inpaint.py              Inpainting masks and what to send for them
    inpaint_math.py         The sums behind inpainting (plain Python, tested)
    inpaint_dialog.py       The Inpaint window
    docker.py               The docker UI
    characters.py           The Characters section of the docker
    references.py           The Precise Reference section of the docker
    doc_state.py            Per-document settings stored in the .kra (plain Python, tested)
    metadata.py             Reading NovelAI's prompt and settings from images (plain Python, tested)
    import_dialog.py        The Import from image dialog
    preview.py              The preview window and result history
    prompt_edit.py          Prompt box with tag autocomplete popup
    tags.py                 Finding the tag at the cursor (plain Python, tested)
    weights.py              Where a prompt is weighted, for colouring it (plain Python, tested)
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

## License

Copyright © 2026 Guppy779.

Released under the [GNU General Public License v3.0](LICENSE). You're free to use, study,
change and share the plugin. If you distribute a modified version, it must stay under the
GPL-3.0 with its source code available.
