# DVD Port Kit

Turns a DVD game (a disc driven by menus and button presses, such as 24: The DVD Board
Game or The Amazing Race DVD Board Game) into a Tabletop Simulator mod.

The port doesn't rewrite the game's logic. It runs the disc's own navigation program in
a DVD virtual machine written in Lua, inside TTS. The disc decides what plays next,
exactly as a DVD player would, including its randomness and its bugs.

```
VIDEO_TS ─ port.py ─┬─ Content\  one MP4 per DVD cell ───────── Unity ─► video bundle
                    ├─ SPU\      button highlights, overlays ── Unity ─► subpicture bundle
                    ├─ manifests\  (for the Unity generators)
                    └─ <name>_global.lua  VM + disc program, all embedded ─► TTS Global
```

In TTS, a disc is four Custom AssetBundle objects:
- **Screen A and Screen B:** the video bundle, twice. Cells alternate between the two
  objects, so the next one starts preparing while the current one finishes. That's how
  cell changes are seamless.
- **Highlight:** the subpicture bundle, showing the highlighted button.
- **Base:** the subpicture bundle again, for always-visible layers. Only some discs
  need this one.

Ports so far: **24** (complete, plays perfectly) and **The Amazing Race** (program and
subpictures exported, dump not yet run).

---------------------------------------------------------------------------------------
## 0. What you need

| | |
|---|---|
| **Python 3.10+** | with **Pillow** (`pip install -r requirements.txt`) |
| **ffmpeg + ffprobe** | on PATH. Any recent build with libx264 and aac. |
| **The disc's files** | `VIDEO_TS` folder, unencrypted (ripped with MakeMKV backup mode, DVD Decrypter, etc.) |
| **Unity** | the project you build TTS asset bundles with: TTS's `TTSAssetBundleEffects` component, and a video template prefab (2a) |
| **Lua 5.2** | *optional*, for the offline tests (`lua5.2` / `lua` on PATH) |

Folder layout the kit expects:

```
P:\DVD Rips\
  DVD Port Kit\            this kit (tools are run from here, never copied into discs)
  <Some Disc>\
    VIDEO_TS\              the rip
    TTS\                   created by port.py: everything generated for the port
```

---------------------------------------------------------------------------------------
## 1. Run the pipeline

```
cd "P:\DVD Rips"
python "DVD Port Kit\port.py" "<Some Disc>" --name sd --title "Some Disc: The DVD Game"
```

`--name` is a short id used in every file name, such as `24` or `tar`. Keep it the same
for the whole port. Every step can be re-run safely. Run a single step again with
`--steps`, e.g. `--steps check,global`.

| Step | What it does | Time |
|---|---|---|
| `program` | Exports the disc's navigation program: every PGC, command and button (read from the disc's own navigation packs, no PGCEdit needed) → `sd_program.txt` | seconds |
| `features` | Checks the disc only uses what the VM emulates. **Read this before dumping a new disc.** | seconds |
| `dump` | One MP4 per cell → `Content\`. Picks up where it stopped, skipping finished cells. | **hours** |
| `manifest` | Builds `manifests\sd_unity.json` (for Unity) and `sd_lua.json` (durations, loop flags) | a minute |
| `spu` | Exports subpictures and button highlights → `sd_spu.txt`, `SPU\img\`, `manifests\sd_spu_unity.json` | minutes |
| `check` | Checks the dump: coverage, clip lengths, tail audio (see §4) | minutes |
| `global` | Builds the TTS Global script → `sd_global.lua` | seconds |
| `test` | Runs a simulated TTS session against the script (skipped without Lua) | minutes |

**Options:**
- **`--jobs N`:** parallel encodes. The default is half your CPU threads.
- **`--crf 24 --abr 128k`:** quality. 24 came out at 784 MB for 220 min of video. For a
  long disc, `--crf 27 --abr 96k` is the next step down. Decide before dumping, because
  changing it later means `--force`.
- **`--unity-folder X`:** where the disc lives in the Unity project, as `Assets/X/Content`
  and `Assets/X/SPU`. It defaults to `--name`; `Assets/X` works too.
- **`--steps scan`:** for a new disc, a full read of the disc that reports buttons or
  subpictures changing *within* a cell. The VM assumes they don't; that held for 24 and
  Amazing Race. Slow.

**Disc bugs.** Put patches in `<Some Disc>\TTS\sd_fixes.txt` (see §5) and re-run
`--steps global`. They're embedded in the script and switched with `CONFIG.FIXES`.

---------------------------------------------------------------------------------------
## 2. Unity

Do each disc in its own folder of the project (`Assets/sd/...`), or in a copy of the
project.

### 2a. Video bundle

1. **Copy the clips in:** copy `TTS\Content\` to `Assets\sd\Content\`, and let Unity
   import it. That's a lot of clips and takes a while.
2. **Set up the prefab,** the same as for earlier ports:
   - **Root:** the screen object, with its MeshFilter and MeshRenderer. Its material
     shows the RenderTexture `VideoOut`, and it carries **TTSAssetBundleEffects**.
   - **`_TEMPLATE PLAYER`:** a child holding only a **VideoPlayer**:
     - Source: Video Clip
     - Play On Awake ✔, Wait For First Frame ✔, Loop ✘, Skip On Drop ✔
     - Update Mode: Unscaled Game Time
     - Render Mode: **Render Texture → `VideoOut`**, Aspect Ratio: Fit Horizontally
     - Audio Output Mode: **Audio Source**, pointing at the communal `Audio Source`
       object. *Direct* output doesn't work in TTS.
3. **Generate:** put `unity\GenerateVideoCues.cs` in an `Editor` folder. Select
   `_TEMPLATE PLAYER`, run **TTS Modding → Generate Video Cue Objects**, and pick
   `manifests\sd_unity.json`.
   - Every looping cue (menus, infinite stills) must have **Loop** set. The kit's
     generator reads the `loop` flag; older copies of the generator ignored it.
   - Effect **0 must be `Idle`**. The manifest puts it first.
4. **Build** the bundle under its own name, for example `sd-video`, and upload it.

### 2b. Subpicture bundle

Skip this if `spu` found no images. Not every disc has highlights.

1. **Copy the images in:** copy `TTS\SPU\img\` and `TTS\SPU\calibrate.png` to
   `Assets\sd\SPU\`.
2. **Build the overlay prefab.** Duplicate the video prefab, then:
   - **Delete:** every `cue_*` object, `_TEMPLATE PLAYER` and the `Audio Source`
     object. Empty the Looping Effects list.
   - **The root's MeshRenderer:** keep it **enabled**, and give it a material that
     draws nothing. Import `unity\clear.png` (Alpha Is Transparency ✔), then create a
     material with the **Unlit/Transparent** shader and `clear.png` as its texture. Use
     that on the root.
     - Disabling the renderer doesn't work: TTS switches it back on, and its empty
       texture covers the video in black.
     - Removing it doesn't work either: TTS substitutes its default cube.
   - **`_TEMPLATE SPRITE`:** add an empty child of the root with a **SpriteRenderer**,
     sprite left empty and the default material.
3. **Generate:** put `unity\GenerateSubpictureCues.cs` in `Editor`. Select
   `_TEMPLATE SPRITE`, run **TTS Modding → Generate Subpicture Cue Objects**, and pick
   `manifests\sd_spu_unity.json`.
   - The generator reads the root's mesh to find where the picture sits. On a box
     mesh, it uses the largest face that faces local −Z, and the console says which
     face it chose.
   - It also sets the PNGs' import settings itself.
4. **Check the alignment:** activate `cue_SPU.Calibrate`. Its red border must trace
   the screen's edges, with TL and TR at the top. Then deactivate it again.
   - If it's wrong, add a child Quad named `_FRAME`, lined up with the picture by
     hand. The generator uses that instead of the root's mesh.
5. **Build** it as, for example, `sd-spu`.

---------------------------------------------------------------------------------------
## 3. TTS

1. **Make four Custom AssetBundle objects:**
   - **Screen A:** the video bundle, placed where the screen goes.
   - **Screen B:** the video bundle again.
   - **Highlight:** the subpicture bundle.
   - **Base:** the subpicture bundle again. Only needed if the `global` step said
     "base overlay used".

   The script locks all of them and snaps them onto Screen A, so place B and the
   overlays anywhere.
2. **Add the script:** paste `TTS\sd_global.lua` into **Global**, fill in the four GUIDs
   at the top, and Save & Play. Fill the GUIDs in **in the file**, not just in TTS's
   editor: rebuilding (`--steps global`) keeps the GUIDs, `FIXES` and `FRAME` from the
   existing file, so every later build pastes straight in. The script is large (24's is 835 KB); an external editor
   plugin handles that better than TTS's own editor.
3. **Start:** the disc starts by itself once the bundles report ready, as a DVD does
   when it's inserted. The console says `ready: N video effects, M subpicture effects`.
4. **Calibrate the click layer once per screen model** (it was already right for 24's
   screen):
   1. Run `lua uiCalibrate()`.
   2. Adjust with `lua setFrame(cx, cy, w, h)` until the red outline sits on the chart's
      border.
   3. Copy the printed `FRAME` values into the script.
5. **Before saving the table, run `lua resetScreens()`.** Each screen re-plays its last
   cue on load, and two screens restoring at once fight over the shared picture. Load
   resets them anyway; this just keeps saves clean.

**Controls:**
- **The picture:** click any button on it.
- **The remote panel,** bottom right: arrows, OK, and Eject, which restarts the disc
  from First Play.
- **Hotkeys:** bind DVD Up, Down, Left, Right and OK under Options → Game Keys.

**Console commands:**

| Command | What it does |
|---|---|
| `lua dvdState()` | Where the VM is: domain, PGC, cell, registers |
| `lua dvdTrace(true)` | Log every cue and link |
| `lua uiDebug(true)` | Tint every clickable button rect |
| `lua resetScreens()` | Everything to Idle/None |

**What isn't saved:** there's deliberately no save/load of game state. A DVD starts
over every time it's ejected, and a half-restored VM is worse than a restart.

---------------------------------------------------------------------------------------
## 4. Checking and debugging

**`check` step.** Run it after every dump, and fix everything it reports before
building bundles.

| Report | Meaning | Fix |
|---|---|---|
| `without a clip` / `.part files` | The dump didn't finish. | Re-run the `dump` step; it resumes. |
| `clips more than 0.1 s from their IFO time` | The cut is wrong. | Re-encode those cells: `tools\dvd_cells.py VIDEO_TS Content --cues X,Y --force`. |
| `end in silence the disc doesn't have` | **Lost tail audio.** The disc stores the end of a cell's audio in the *next* cell's sectors, and a dump that missed it has a silent hole at every seamless join. The kit's `dvd_cells.py` reads past the cell for it; this check catches dumps made by older copies. | `tools\dvd_cells.py VIDEO_TS Content --tails` |

**The Test Player** (`player\DVD VM Test Player.html`) plays a disc in Chrome or Edge,
with no Unity or TTS. Open it, pick the disc's `TTS` folder, and play.
- **What it shows:** every command it runs, the registers, the buttons with their
  highlights, and the subpictures.
- **What it's for:** understanding a disc, finding bugs, and testing fixes. Use **Jump**
  to go straight to any PGC.

**Offline tests (Lua 5.2):**
- **`tests\test_tts_global.lua`:** the simulated TTS that the `test` step runs.
- **`tests\fuzz_dvdvm.lua`:** random-input soak test on the program,
  `lua5.2 tests\fuzz_dvdvm.lua TTS\sd_program.txt 300000 1`. It reports dead ends and
  missing PGCs. That's how both of 24's real disc bugs were found.
- **`tests\test_dvdvm.lua`:** scripted paths, `lua5.2 tests\test_dvdvm.lua <program> "k d d k" 1`.
  `cN` clicks button N, `u` `d` `l` `r` `k` are arrows and OK, and `wN` lets N clips end.

**`tests\seam\`** is the kit that proved preroll in TTS: tone clips, frame-labelled
slices, and a two-screen test script. You only need it if playback itself ever has to
change.

---------------------------------------------------------------------------------------
## 5. Disc bugs and fixes

Discs ship with bugs. 24 has a dead end at Mission Briefing in about 1 game in 24, plus
a fixed agent order that was meant to be random. Amazing Race has a quick-start
shortcut that skips its reset. Patch them without touching the export, in
`TTS\sd_fixes.txt`:

```
P <pgc> <pre|post|cellcmd> <index> <hex>
P M1.27 pre 1 7800000ffffa0000           replace pre command 1 of PGC M1.27
P M1.53 post 0 7100000150000000...       replace the whole post section (index 0)
```

- **PGC keys:** come from the program export (`M<n>.<pgc>` for menus, `V<n>.<pgc>` for
  titles), the Test Player's trace, or `fuzz_dvdvm.lua` output.
- **Commands:** 16 hex digits each, in standard DVD VM encoding. The Test Player shows
  every command decoded; copy an existing one and change it.
- **Write it down:** record each fix and why in a Disc Logic document. See
  `docs\Example - 24 Disc Logic.md` for the format that worked.
- **Ship as authored:** set `CONFIG.FIXES = false` in the script to play the disc
  exactly as it was made.

---------------------------------------------------------------------------------------
## 6. Troubleshooting

Each of these happened once. They're fixed in the kit; the table is here so they're
recognised if they come back.

| Symptom | Cause | Fix |
|---|---|---|
| Silent gap at each cell change; the browser player stalls audio too | The dump lost tail audio (§4) | `dvd_cells.py --tails` |
| Picture freezes briefly between cells | Single-screen playback: a clip only starts preparing once the last one has ended | Use the kit's script; it alternates Screen A and B. Both must load the **same** video bundle. |
| `VM Error ... Trying to access Non-Existent Bits` | TTS's Lua (MoonSharp) `bit32.extract` rejects fields that reach bit 31 | Fixed: the VM does bit operations in plain arithmetic. Don't reintroduce `bit32`. |
| Menus play once and freeze | Clips not set to loop | Regenerate with the kit's `GenerateVideoCues.cs`, which reads `loop`. |
| Black frame over the video, subpictures on top | Overlay root's MeshRenderer disabled, and TTS re-enabled it | Keep it enabled, with the clear Unlit/Transparent material (2b). |
| TTS cube instead of the overlay | Overlay root's MeshRenderer removed | Same: keep it, with the clear material. |
| Highlights misplaced, mirrored or rotated | The frame mesh's UVs don't match how the video is shown | Add a hand-aligned `_FRAME` Quad (2b step 4). |
| Clicks land beside the buttons | Click layer not calibrated for this screen model | `lua uiCalibrate()` (§3). |
| Two pictures flicker after loading a save | Both screens restored a cue | `lua resetScreens()` before saving. |
| `no effect 'X'` in the console | The clip is missing from the bundle, or the bundle is out of date | Re-run `check`, then regenerate in Unity. |
| Warm, hold or speed tricks for seamless playback | Don't: with both screens sharing one RenderTexture, a frozen or slowed player draws over the visible one | Preroll needs nothing beyond what the kit does. |
| `Couldn't find VIDEO_TS\VIDEO_TS.IFO` | Wrong folder; the message lists what's in it | Point at the folder that *contains* VIDEO_TS (or VIDEO_TS itself). A trailing `\` is fine: port.py strips the stray quote PowerShell 5 adds. |
| Multiplayer: some cell changes don't happen for players other than the host | Asking an object to play the effect it's already on restarts it on the host only | Fixed: a repeat goes via `Idle` first (`CONFIG.RETRIGGER_FRAMES`, default 3). If it still happens, raise it to 6–10. |
| Lua memory or freeze when loading data | `JSON.decode` on large payloads | Everything is embedded as plain text lines; keep it that way. |

---------------------------------------------------------------------------------------
## 7. Limits

The VM runs DVD command groups 0–3, which covers everything both ported discs use:
- compare and goto;
- all links and jumps, including CallSS and RSM;
- SetSTN and SetHL_BTN;
- every Set operation.

The `features` step lists anything a new disc uses beyond that:

| Feature | What happens now |
|---|---|
| Command groups 4–7 | Skipped, with a log line. |
| Navigation timers (SetNVTMR) | Ignored. |
| Counter-mode registers | Treated as plain registers. |
| Karaoke mixing, audio and angle streams | The dump has one audio track and one angle. |

The `scan` step finds button sets or subpictures that change *within* a cell. The VM
and the subpicture export assume one per cell.

If a disc needs any of these, the VM gets that feature added for that disc. That's
deliberate: features are added as discs need them, which keeps the VM small.

---------------------------------------------------------------------------------------
## 8. Files

| | |
|---|---|
| `port.py` | the pipeline driver |
| `tools\dvd_cells.py` | cell dumper (sectors → MP4, cut on NAV timestamps, tail audio, `--tails`, `--cues`, `--force`) |
| `tools\dvd_program.py` | program export |
| `tools\build_dvd_manifest.py` | video manifests (loop detection) |
| `tools\dvd_spu.py`, `dvd_spu_export.py` | subpicture decoding and export |
| `tools\build_spu_manifest.py` | subpicture manifest + `calibrate.png` |
| `tools\check_program.py`, `check_dump.py` | the checks |
| `tools\nav_scan.py`, `spu_scan.py` | the scans |
| `tools\dvdvm.lua` | the DVD VM |
| `tools\dvdgame_vm_host.lua` | the TTS host template |
| `tools\build_tts_global.py` | assembles `<name>_global.lua` |
| `unity\` | Unity generators and `clear.png` |
| `tests\` | offline tests and the seam test kit |
| `player\` | the browser Test Player |
| `docs\` | design notes from 24 / Amazing Race (*why* everything is the way it is) and an example Disc Logic write-up |
