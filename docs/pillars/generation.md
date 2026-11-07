# Image Generation

**Goal**: a picture asked for is a picture delivered, whatever ComfyUI does in between.

**Status**: built, unit-tested and running on this machine (2026-10-23, `feature/comfyui-generation`;
REQ-000000433 to REQ-000000439). It also passed a live run against this machine's ComfyUI: three icons in one
batch, a fresh process, 52 s restart, 146 s render. The pillar is optional and off by default. Recipes (with the
built-in `pbr-texture`), every picture's node setup, the duplicate check and the ComfyUI hand-over were added
on 2026-10-24 (REQ-000000453 to REQ-000000457); they are unit-tested, not yet run live. The two-step PBR route
(`seamless-texture`, then `pbr-maps` on the chosen pictures: recipes that take a picture) followed the same day
(REQ-000000458 to REQ-000000460), unit-tested, not yet run live.

## Why it exists

ComfyUI on this machine (Windows, AMD GPU, Flux schnell Q4 GGUF) renders the first graph of a process
correctly and the next one as flat grey: the sampler produces NaN, and the PNG is a uniform 512x512 picture
of about 3.3 KB. Freeing memory, tiled decoding and SDXL did not help. Restarting ComfyUI between graphs and
putting many pictures in one graph did (little-shop-of-things, `docs/ui_overhall_1/ui_decisions/`,
2026-10-22). This pillar turns that recovery into a service. Callers queue prompts and later download pictures,
and the restarts, retries and batching happen without them.

## Concepts

- **Request**: a set of prompts for one project, answered at once with an id and how to probe it. Its status
  is `queued` (no picture started), `running`, `success`, `failed` or `cancelled`.
- **Image**: one picture of a request. Its settings (model, prompt, size, steps, cfg, sampler, LoRA and seed)
  are resolved when the request is taken: the model's defaults apply where the prompt and the request's
  `defaults` say nothing, and a missing seed is drawn and recorded. A prompt with `count: n` gives n images
  with seeds counting up. The image stores the ComfyUI graph it renders, filled then, so a retry renders the
  same graph and its node setup can always be shown ("Node setup").
- **Recipe**: a saved ComfyUI node setup a prompt renders instead of a model's text-to-image graph ("Recipes").
- **Outputs**: the files of one picture, one per SaveImage the graph keeps, each with a role (`texture`,
  `normal`, ...). A model's picture has one; a `pbr-texture` picture has seven. The first is the **primary** one.
- **Source**: the done picture an image of a recipe with an `image` slot takes as its input ("Pictures as
  input").
- **Batch**: the pictures rendered together as one ComfyUI graph in one fresh process.

## The queue

One worker thread renders one batch at a time (`generation/worker.py`):

1. **Batching** (`planner.plan_batches`). Take the oldest image still to render, then every later image of the
   same model (or the same recipe, keyed `recipe:<name>`) in queue order, from any request, up to
   `max_batch_images` (default 16). The batch's graph is every image's stored graph, node ids prefixed, with
   each node two images compute identically shared (`recipes.merge`): the model is loaded once, each LoRA
   strength once, an identical prompt encoded once, and every picture keeps its own sampler and SaveImages,
   which write to `lws/<request>/<idx>[-<role>]`. A request never waits behind a later request of its own model.
2. **Restart**. Before each batch, ComfyUI is restarted through its supervisor, unless the process has
   rendered nothing since its last restart. A restart that does not bring ComfyUI up is tried again, up to
   `max_restart_failures` times in a row (default 3), 10 s apart.
3. **Render**. The graph is submitted to `/prompt`, `/history/<id>` is polled until it completes, and every
   output of each picture is fetched through `/view`. The timeout is `batch_timeout_seconds` plus `image_timeout_seconds` per
   picture.
4. **Check** (see "Bad outputs"). A good picture's outputs are stored in
   `~/.local/state/little-workshop/generated/<request>/`, each with its SHA-256. A bad picture, one missing any
   of its outputs, or every picture of a graph that failed, goes back to the queue with its attempt counted, to
   render in the next fresh process.
5. **Failure**. A request fails only when one of its pictures has used up `max_attempts` (default 3, each in a
   fresh process), or when ComfyUI could not be restarted at all. Its other pictures stop, and the queue
   **pauses**: nothing renders until the user resumes it (`resume`, optionally `retry_failed` to queue the
   failed requests' pictures again with their attempts reset). The pause is reported in the queue, in every
   open request's status and on the Dashboard's attention list, and the
   `generation.queue_paused` event is emitted (a workshop trigger can react to it).

A picture left running by a stopped pillar goes back to the queue at the next start, without counting the
attempt.

## Bad outputs

`generation/imagecheck.py`, standard library only. A picture is bad when ComfyUI saved none, when it is not an
8-bit PNG the module can read, or when it is both:

- **small**: its compressed pixel data is under `bad_output.suspect_ratio` (default 5%) of its raw size. A grey
  picture is about 0.2%. Real ones measured 2.8% (a snowy banner) to 26% (an icon on a flat cream background).
- **grey**: the spread of its luminance (sampled on every fourth pixel of every fourth row) is under
  `bad_output.max_stddev` (default 4.0 out of 255). Grey pictures measure 0.0. Only a small picture is decoded,
  in about 0.03 to 0.07 s.

Measured on the 141 ComfyUI outputs of little-shop-of-things (2026-10-23): all 31 grey pictures were bad and
all 110 others were good.

Only a picture's primary output is checked this way: a flat normal or metallic map is grey on purpose.

**Duplicates** (REQ-000000457). Pictures of one batch whose primary outputs are byte-identical are all bad when
their graphs compute different things (SaveImage names aside). Different seeds never give the same bytes: on
2026-10-24 SDXL at 2048x2048 rendered 9 of 10 pictures of request 323e2363 as two seed-independent stripe
patterns, which the grey check passed (noise compresses badly). Two pictures with the same graph may match.

## Estimates

A batch costs a restart, an overhead (loading the model) and a time per picture. The restart time is the mean
of the measured restarts. The overhead and the time per picture come from a straight line through each model's
measured (pictures, render seconds), once two batch sizes are known; before that, the configured overhead and
the mean time per picture are used, and before any batch, `estimates`. A request's estimate runs the batching
rule over the queue: the rest of the running batch, then each batch ahead of the request's last picture. It
assumes no picture renders twice, and says so. While the queue is paused, the estimate says that nothing
renders until the queue is resumed.

## Choosing a model

A caller picks the model that fits the picture and the task (REQ-000000445). `GET /v1/models[?purpose]`
(`generation/models.py`; `workshop_generation_models`, `lws generation models`) lists every configured model with:

- what it is for (`use_for`, in the words of `models.PURPOSES`: icon, ui, concept-art, texture,
  material-reference, scene, character, sprite, background) and what it is bad at (`avoid_for`);
- its licence and `commercial_use` (`yes`, `no` or `unclear`);
- how to prompt it, its LoRAs (name, file, trigger words, purpose), its default settings;
- its speed: the measured seconds per picture and per batch, else the configured estimates;
- `installed`: whether its files are in ComfyUI's model folder (`comfy_models_dir`, read as files, ComfyUI
  stays off), with the missing files; unknown when the folder is not configured.

With a `purpose`, the models are ranked (installed first, then made for that purpose by themselves or through a
LoRA, then cleared for commercial use) and one is recommended, with the LoRA and its trigger words when a LoRA is
what fits; when no installed model fits, the recommendation says so instead of naming one. `not_yet_supported`
lists what the service cannot do yet, with the files already installed for it: ControlNet guidance, normal and
depth maps (MoGe), upscaling, a style reference, seamless tiling and image-to-image. A request for a model whose
files are known to be missing is refused at submit.

The models on this machine (2026-10-24): `flux` (Flux.1 schnell Q4 GGUF, Apache-2.0; with the `icon-kit` LoRA
for icons and UI, without it for concept art, scenes and backgrounds), `sdxl-base` (SDXL 1.0 base with the fp16-fix
VAE, OpenRAIL++; textures, material reference, concept art, environments) and `sdxl` (Game Icon Institute XL,
licence unclear, private experiments only). A `checkpoint` model may name its own `vae`.

## Recipes

`generation/recipes.py` (REQ-000000453). A recipe is a ComfyUI graph in API format (what ComfyUI's
Workflow > Export (API) writes) with:

- **slots**: the inputs a prompt fills, `{slot: [node id, input]}`, among `prompt`, `negative`, `seed`,
  `width`, `height`, `steps`, `cfg` and `image` (a `LoadImage` node's picture). A recipe has a `prompt` or an
  `image` slot. A slot the request leaves out keeps the recipe's value.
- **outputs**: the SaveImage nodes whose pictures the request gets, `[{role, node}]`; SaveImages not listed are
  dropped from the rendered graph.

They come from three places:

- **Built in**: `config["recipes"]`, three for PBR textures, built from two node groups in `config.py`:
  - `seamless-texture` renders a seamless SDXL texture (`SeamlessTile` on the model, `CircularVAEDecode`,
    ComfyUI-seamless-tiling), 1024x1024 by default. Its description suggests Ubisoft's prompt wording ("texture
    of …, top down view, seamless tileable").
  - `pbr-maps` takes a picture (`LoadImage`, its `image` slot) and derives its maps: albedo and the ORM split
    into occlusion, roughness and metallic with SuperMat (ComfyUI-SuperPBR), normal and height with DeepBump
    (comfy_mtb's `Deep Bump (mtb)`). Albedo is its primary output: the one the grey check can judge.
  - `pbr-texture` is both in one graph: the texture and its seven maps from a prompt.

  Every model and node is MIT, GPL-3.0 (code only; outputs unrestricted) or OpenRAIL++, all allowing commercial
  use; Ubisoft's CHORD was not used, its licence being research-only. Its pipeline confirmed the shape: a
  tileable SDXL texture at 1024, then image-to-material as its own step.
- **Saved** from a rendered picture: its stored graph, with the slots of the recipe it came from or suggested.
- **Imported** from ComfyUI: a pasted or uploaded Export (API) file. `suggest` proposes the slots (the first
  sampler's seed, steps and cfg; the text of the encoders feeding its positive and negative; its latent's size;
  its only LoadImage)
  and the outputs (every SaveImage, roled from its filename prefix), and the user confirms or changes them. The
  UI workflow format is refused with that advice.

A request names a recipe per prompt (`recipe`, also in `defaults`). Model-only settings (`model`, `lora`,
`sampler`, ...) are refused with a recipe, as is a value for a slot it lacks, and `count` above 1 without a
seed slot. The model files a recipe loads (inputs such as `ckpt_name`, plus its `files`) are checked against
`comfy_models_dir` at submit. A built-in recipe cannot be replaced or deleted.

### Pictures as input

REQ-000000458, REQ-000000459. PBR textures take two steps: render a few `seamless-texture` pictures, keep the
good ones, then `pbr-maps` on them, so only the chosen textures pay for the material pass.

- An image of a recipe with an `image` slot names its **source**, `{request_id, idx, role?}`: a done picture of
  any request, `role` picking one output of a picture with several (its primary one by default). Its `prompt`
  is optional and only describes the image; it defaults to the source's, and the file label is the source's.
- When the request is taken the source is refused unless it is done and its file is in little-workshop's store
  (copies not deleted); so is a source given to a recipe without an `image` slot or to a model, a recipe with
  one that has no source, and an unknown role.
- The stored graph's `LoadImage` names `lws/<request>-<idx>[-<role>].png` (`recipes.input_name`). Before
  submitting a batch the worker uploads each source to ComfyUI's input folder under that name
  (`POST /upload/image`, overwriting), so the graph renders as stored, and a picture's `workflow.json` loads in
  ComfyUI during a hand-over.
- Several sources batch together: each gets its own `LoadImage`, and `merge` shares the loaders and the SuperMat
  model.
- A source whose file is gone when its batch is taken fails that image and its request with the reason; the
  rest of the batch renders and the queue does not pause, since nothing is wrong with ComfyUI.
- The same source made into maps twice gives the same bytes; the duplicate check passes it, as the two graphs
  compute the same thing.

## Node setup

Every picture's graph is stored when its request is taken (REQ-000000456); a picture queued before that rebuilds
it from its settings. `GET /v1/requests/{id}/images/{idx}/graph` gives the graph (API format, what ComfyUI
loads), its outputs and its nodes as rows (class, title, values, links). The Dashboard's picture page shows
those nodes, every output by role, a download of the graph (drop it on ComfyUI to open it) and Save as recipe.

## Hand-over

ComfyUI's web app is lent to the user from the Images page (REQ-000000455), because the queue restarts ComfyUI
before every batch and stops it when idle, which would end the user's work:

- **Lend** (`POST /v1/comfy/lend`): records the hand-over. Once no batch renders, the worker restarts ComfyUI
  for the user (a fresh process), and from then on renders nothing and neither restarts nor stops it.
- **Activity**: every idle tick the worker reads ComfyUI's `/queue` (a graph running or waiting) and the newest
  `/history` entry; either changing is activity. After `lend_idle_minutes` (30) without any, ComfyUI goes back
  to the queue by itself.
- **Restart** (`POST /v1/comfy/restart`): a fresh process for the user, since a process renders its first graph
  cleanly and later ones may come out grey.
- **Give back** (`POST /v1/comfy/return`): the queue's next batch restarts ComfyUI; with nothing queued, it is
  stopped, freeing the GPU.

`GET /v1/comfy` reports the state (`queue`, `waiting for the batch`, `starting`, `lent`, `error`), the hand-over
and the last one, and the address the browser opens: `comfy_public_url`, a `tailscale serve` of ComfyUI, so the
web app and its websocket reach ComfyUI directly.

## GPU usage

The GPU is shared: ComfyUI renders the queue's pictures on it, and the Unity Editor, Blender, the desktop and
whatever an agent starts use it too. Nothing locks it. `GET /v1/gpu` (`generation/gpu.py`) says how busy it is
so that a caller decides for itself whether to go ahead, wait, or do other work first (REQ-000000442):

- **Sample**: one PowerShell call through WSL interop reads Windows' performance counters, the ones Task
  Manager reads. It gets each GPU engine's utilization per process, each process's dedicated and shared memory,
  the adapters' memory, the adapter's name and size (registry) and the RAM. It takes about 3 s. It is taken only
  when asked and kept `gpu.cache_seconds` (15), so callers asking together share one; `cached_only` never takes
  one (the Dashboard's page load).
- **Report**: the busiest adapter's utilization (its busiest engine, every process on it summed, as Task
  Manager shows it), utilization per engine type, the memory in use (dedicated and shared), the top 8
  processes by name with ComfyUI's marked (the supervisor's pids), what the generation queue will do (rendering,
  pictures waiting, `idle_in_seconds`) and advice in words.
- **Level**: `idle`, `light`, `busy` or `saturated`, the higher of what the busiest engine (10, 40, 85 %) and the
  memory in use say (0.5, 0.75, 0.9 of what it may use). Memory counts dedicated and shared together against
  both totals: this machine's integrated GPU (AMD Radeon 8060S) has 4 GiB dedicated and spills into shared
  memory, up to half the RAM, so the dedicated part alone reads as full most of the time.
- **Unknown**: without `powershell.exe` or readable counters, the report says the usage is unknown and why.

It is `workshop_generation_gpu`, `lws generation gpu` and the GPU card of the Dashboard's Images page ("Measure
now"). Every project's shared briefing tells agents to read it before GPU work.

**Stopping when idle** (REQ-000000443). An idle ComfyUI keeps its model in GPU memory: the one the 2026-10-23
live run left held 8.1 GiB. Every batch restarts ComfyUI anyway, so once the queue has nothing to render (empty
or paused) the worker stops ComfyUI through the supervisor's `POST /stop`, if this pillar restarted it since it
last stopped it (`stop_when_idle`, default true). A ComfyUI someone else started is left running.

## Downloads and cleanup

A successful request's status carries:

- `download.files`: each picture's URL, SHA-256, size, prompt and seed. Files are named
  `<index>-<label>.png`, where the label is what tells the prompt apart from the request's others (shared
  leading and trailing words removed).
- `download.wget`: one command that downloads every picture (`wget -i urls.txt`), fetches `SHA256SUMS` and runs
  `sha256sum -c`.
- `cleanup`: the MCP tool, `lws` command and HTTP call that delete little-workshop's copies, with the advice
  to delete only after wget exited 0 and every checksum printed OK. Afterwards, the files and lists are no
  longer served.

The Dashboard's Images page (`/generation`, docs/pillars/dashboard.md) shows the same: the queue, every
request a page at a time, each request's pictures, and the user's decisions (resume, cancel, delete the copies once
the download is checked). Its New images form (`/generation/new`) is the user's own way to queue pictures.

The URLs are the Dashboard's (`dashboard_url`, default `http://localhost:8790`, the loopback bridge; the
Tailscale address when set to it). The Dashboard passes `/generation/<id>.json`, `/files/<name>`, `/urls.txt`,
`/SHA256SUMS` and `DELETE /files` on to this pillar, which holds the bytes.

## ComfyUI

`generation/comfy.py` reaches ComfyUI (`comfy_url`, default `http://127.0.0.1:8188`) and the ComfyUI
supervisor (`supervisor_url`, `http://127.0.0.1:8189`, `C:\Users\crist\comfyui_supervisor\supervisor.py`) over
HTTP only. WSL's mirrored networking reaches both on 127.0.0.1, and no file path is shared. The supervisor's
`POST /restart?wait=1` kills every ComfyUI process, launches a fresh one and answers once it is ready. When
the supervisor does not answer, `supervisor_launch` (if set) is started detached and the restart tried once
more. On WSL a closed loopback port times out instead of refusing, so a connect timeout counts as "not
running" too. With `comfy_output_dir` set (ComfyUI's output folder as seen from WSL), ComfyUI's own copy of
each picture, grey ones included, is deleted once the pillar holds it.

## Interface

Pillar Interface on `generation.sock`:

| Route | What |
|---|---|
| `POST /v1/requests` | Submit `{project_id, prompts: [string or {prompt, negative, model, width, height, seed, steps, cfg, sampler, scheduler, lora, lora_strength, recipe, source, count}], defaults, name}` |
| `GET /v1/requests[?project_id&status&limit&offset]` | Requests, newest first, each with its estimate; `offset` skips the newest ones (paging) |
| `GET /v1/requests/{id}[?project_id&images]` | Status, estimate, downloads, cleanup; `images=true` lists every picture with its resolved `settings` |
| `GET /v1/requests/{id}/wait[?project_id&timeout]` | Long poll: answers once the request is no longer open, the queue pauses, or `timeout` seconds pass (default 300, at most 1800), with `finished` and `waited_seconds` |
| `POST /v1/requests/{id}/cancel` | Cancel a queued or running request; pictures already rendered are kept |
| `DELETE /v1/requests/{id}/files` | Delete little-workshop's copies of a finished request |
| `GET /v1/requests/{id}/files/{name}`, `/urls.txt`, `/SHA256SUMS` | What the Dashboard serves for wget |
| `GET /v1/requests/{id}/images/{idx}/graph` | The picture's graph, outputs and nodes ("Node setup") |
| `GET /v1/recipes`, `GET/DELETE /v1/recipes/{name}` | Recipes with their slots, outputs and installed state; one with its graph and nodes |
| `POST /v1/recipes` | `{name, title, description, from_image: {request_id, idx}` or `graph, slots, outputs, replace}` |
| `POST /v1/recipes/suggest` | `{graph}`: the slots and outputs it would get, and its nodes |
| `GET /v1/comfy`, `POST /v1/comfy/lend\|restart\|return` | The hand-over ("Hand-over") |
| `GET /v1/queue` | Pause, hand-over, running batch, open requests with estimates, next batches |
| `GET /v1/models[?purpose]` | The models to choose from, ranked for a purpose ("Choosing a model") |
| `GET /v1/gpu[?max_age&cached_only]` | The GPU usage report ("GPU usage") |
| `POST /v1/queue/resume` | `{retry_failed}`: the user's decision |
| `POST /v1/jobs/generation.purge_project` | A removed project's requests and files |

Given `project_id`, a route refuses a request belonging to another project (404). The same operations are
`lws generation submit|status|list|queue|models|gpu|resume|cancel|delete-files|recipes|recipe-show|recipe-save|recipe-import|recipe-delete|setup`
and the `workshop_generation_*` MCP tools (`workshop_generation_recipes`, `workshop_generation_recipe_save` and
`workshop_generation_picture_setup` among them); `lws generation submit --recipe pbr-maps --from <request>/<idx>[/<role>]`
names sources. The hand-over is the user's, on the Dashboard only.

Two MCP tools serve an agent working in a project (REQ-000000441):

- `workshop_generation_wait` calls the long poll (default 600 s, at most 1800 s); not finished, it is called
  again.
- `workshop_generation_download` writes a successful request's pictures into a folder of the project's checkout
  (the Registry's `path`; a folder outside it is refused), fetching each from the pillar's socket and checking
  its SHA-256 as fetched and as written. A file already there with the same checksum is kept, one with another
  is refused unless `overwrite`. With every file verified, it points at `workshop_generation_delete_files`.

Every project's shared briefing (`.claude/little-workshop.md`, `registry/claude_templates.py`) names submit, wait
and download, so a project's agents know the service exists.
In a project's Claude session the reads, wait, download and cancel are pre-allowed; submit, resume and
delete-files always prompt (`registry/claude_templates.py`).

## Asking for pictures

Images are generated only when the user asked for them, or after asking the user (REQ-000000444). Rendering
takes the GPU for minutes, so an agent never starts it on its own initiative:

- **The user**: the Dashboard's New images form (`/generation/new`) queues a request directly; pressing it is
  the asking.

- **Briefing**: every project's shared briefing (`.claude/little-workshop.md`) says so, and names submit, wait,
  download and the GPU report.
- **Interactive sessions**: `workshop_generation_submit` and `lws generation submit` ask for permission, like
  resume and delete-files.
- **Agent tasks** run without permission prompts, so the project's guard (`lws_scope_guard.py`) decides there
  (a hook event with `permission_mode` `bypassPermissions`). It refuses submit unless the user allowed image
  generation for that task: the task option `allow_image_generation` (New task's "May generate images",
  `workshop_agent_run_task`) puts `LITTLE_WORKSHOP_IMAGE_GENERATION=allowed` in the task's environment. Refused,
  the agent says in its report which pictures it would generate. Resume and delete-files are always refused
  there: they are the user's decisions.

Tests never reach ComfyUI, its supervisor or the GPU counters: an autouse fixture in
`tests/unit/test_generation.py` fails any that would.

Example: a set of icons, then the download.

```
lws generation submit --project-id little-shop-of-things --lora icon-kit --width 512 --height 512 \
  --prompt "Icon Kit, a single small game UI icon: a teapot, thick dark brown ink outline, plain cream background" \
  --prompt "Icon Kit, a single small game UI icon: a lantern, thick dark brown ink outline, plain cream background"
lws generation status --project-id little-shop-of-things <id>      # queued/running: estimate; success: download.wget
```

## Configuration

`~/.config/little-workshop/generation/config.json`, merged key by key over `generation/config.py`'s
`DEFAULTS`: the URLs, `supervisor_launch`, `comfy_output_dir`, batch size, attempts, restart failures,
timeouts, `max_images_per_request` (500), `comfy_models_dir`, `stop_when_idle`, `gpu`, `bad_output`, `estimates`,
`comfy_public_url`, `lend_idle_minutes` (30), `default_model`, `models` and `recipes` (merged recipe by recipe). Each model is
a `flux_gguf` (`unet`, `clip_l`, `t5`, `vae`) or a `checkpoint` (`checkpoint`), with defaults for steps, cfg,
sampler, scheduler, size, negative and its `loras` by name. The defaults describe this machine: `flux` (Flux
schnell Q4 GGUF, 4 steps, euler/simple, cfg 1, LoRA `icon-kit` at 0.9) and `sdxl` (Game Icon Institute XL,
22 steps, dpmpp_2m/karras, cfg 6). Read at every batch and request, so a change needs no restart.

## Deployment

The pillar is `python -m generation` (Bazel `//:generation_server`). Like every optional pillar, it starts
once the Core manifest has an entry:

```toml
[pillars.generation]
enabled = true
command = ["/home/crist/.local/share/pipx/venvs/little-workshop/bin/python3", "-m", "generation"]
```

The MCP tools, the `lws` commands and the Dashboard routes need the MCP server, `lws` and the Dashboard from
the same version. On this machine the pillar's `config.json` sets `supervisor_launch` to
`["/mnt/c/Users/crist/AppData/Local/Programs/Python/Python312/pythonw.exe",
"C:\\Users\\crist\\comfyui_supervisor\\supervisor.py", "serve"]` (Windows' Python through WSL interop, so a
supervisor that is not running is started) and `comfy_output_dir` to `/mnt/c/Users/crist/ComfyUI/output`.

The PBR recipes need, in `C:\Users\crist\ComfyUI` (installed 2026-10-24): the custom nodes comfy_mtb (DeepBump;
its `onnxruntime`, not `onnxruntime-gpu`, which is CUDA-only), ComfyUI-SuperPBR and ComfyUI-seamless-tiling in
`custom_nodes`, and `models/deepbump/deepbump256.onnx`, `models/diffusion_models/supermat.pth` and
`models/vae/vae-ft-mse-840000-ema-pruned.safetensors`. The hand-over's address is the Tailscale Service `svc:comfyui`
(defined in the Tailscale admin console, then `tailscale serve --bg --service=svc:comfyui --https=443
http://127.0.0.1:8188`), and its `https://comfyui.<tailnet>.ts.net` is set as `comfy_public_url`. ComfyUI refuses a request
whose Host is loopback and whose Origin is not: if the served page answers 403, the proxy rewrote the Host.
Source pictures land in `ComfyUI/input/lws/`, one file per source picture used (overwritten by name); purging a
project does not reach into ComfyUI's folders.

## Interfacing

- **Calls**: Core (socket discovery, by every caller), Orchestration (`/v1/events`: `generation.queue_paused`,
  `generation.request_succeeded`), ComfyUI and its supervisor (HTTP).
- **Called by**: `lws`, the MCP server, the Dashboard (downloads, attention list), Registry & Portfolio's
  removal (`generation.purge_project`, when that cascade is wired; it is not yet, as for the other pillars).
- **Not an Orchestration Job**: a request is a queue entry of this pillar, not a Job, because a batch mixes
  requests and a request's status, estimate and pause belong to the queue as a whole. Restarting ComfyUI is
  this pillar's own recovery, not a Deployment & Servers process: ComfyUI runs in Windows and its supervisor
  owns it.
