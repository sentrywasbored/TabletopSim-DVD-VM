# Amazing Race + 24 — TTS port plan (DVD VM approach)

Both discs are driven by thousands of VM commands and auto-action buttons (24: 6,543
buttons, 623 auto; Amazing Race: 4,996 buttons, 892 auto). Hand-porting that logic the
way Khufu/Gatekeeper were ported isn't realistic. Instead, the port **runs each disc's own
navigation program** in a small Lua DVD VM. Video is dumped once per cell, and the VM
picks the cue.

```
VIDEO_TS ──dvd_cells.py──► Content/<domain>/V###C##.mp4 ──► Unity (GenerateVideoCues.cs) ──► bundle
    │                              └─build_dvd_manifest.py──► <name>_unity.json / <name>_lua.json
    └─dvd_program.py──► <name>_program.txt  (+ <name>_fixes.txt) ──► dvdvm.lua in Global
```

All scripts are in each disc's `TTS\` folder.

---------------------------------------------------------------------------
## 1. Dump (run on your PC — the linked VM can't hold a long job)

```
cd "P:\DVD Rips\24 The DVD Board Game\TTS"
python dvd_cells.py ..\VIDEO_TS Content --jobs 6
python dvd_program.py .. 24_program.txt --name 24
python build_dvd_manifest.py Content 24_program.txt --name 24 --out-dir manifests

cd "P:\DVD Rips\The Amazing Race DVD Board Game\TTS"
python dvd_cells.py ..\VIDEO_TS Content --jobs 6 --crf 27 --abr 96k
python dvd_program.py .. tar_program.txt --name AmazingRace
python build_dvd_manifest.py Content tar_program.txt --name tar --out-dir manifests
```
- **Re-running resumes.** Finished cells are skipped and `.part.mp4` files are redone. `--only VTS05`
  and `--force` are there for spot fixes.
- **One file per unique cell, cut from the VOB sectors directly** (no PgcDemux):
  - each cell is trimmed to its own NAV-pack start/end timestamps;
  - it is shifted by the cell's start rather than the first decoded frame, so an open GOP
    can't slide video against audio;
  - it is padded to the IFO cell time.
- **Verified on test cells:** every test cell came out at exactly its IFO length on both
  streams, 2-second cells included, and the output starts at 0.
- **Encode settings:** Gatekeeper/Khufu, at 640×480 (both discs are 4:3 NTSC), 29.97 fps, with
  bwdif + light hqdn3d. Content also gets `Idle.mp4` (0.2 s black) for bundle index 0.

**Size, measured on every 40th / 80th cell:**

| Disc | Cells | Runtime | CRF 24 / 128k | CRF 27 / 96k |
|---|---|---|---|---|
| 24 | 1,239 | 220 min | **~560 MB** | — |
| Amazing Race | 2,432 | 437 min | ~1.8 GB | **~1.3 GB** |

A side-by-side frame from the busiest Amazing Race cell showed no visible difference
between the two settings. If 1.3 GB still downloads badly, CRF 28 / 80k is the next step.
The fallback is splitting Amazing Race into two bundles (hub/menus vs. the city challenge
VTSs), since the VM only needs cue names.

---------------------------------------------------------------------------
## 2. The program file (`*_program.txt`, ~400 KB each)

Line-based text, decoded lazily per PGC. There's no JSON, so none of the `JSON.decode` freezes:
- `T` titles
- `R` PTT tables
- `G` one per PGC: nav links, still, pre/post/cell commands as raw hex, and cells as
  `cue:still:cellcmd:seconds`
- `B` one per cell with buttons: forced select/action, highlight window, and each button's
  rect / udlr / auto / command

Every button on both discs is live for its whole cell, so the VM needs no sub-cell timing.

**Fix files:** `P <pgc> <section> <index> <hex>` lines appended to the program patch commands
without touching the export:
- `24_fixes.txt`: randomises the fixed 3-agent order, and gives the v1m53 dead end (§4) its
  missing post commands.
- `tar_fixes.txt`: makes v3p4/v3p7 roll `rnd(65535)` instead of the constant.

Leave them out to ship the disc exactly as authored.

---------------------------------------------------------------------------
## 3. dvdvm.lua (written and tested headless)

The VM implements libdvdnav semantics for everything these discs use, which is command
groups 0–3 only:
- **Commands:** Goto, Break, all Link/LinkSIns forms including "(button n)", JumpTT,
  JumpVTS_TT, JumpVTS_PTT, JumpSS and CallSS to FP/VMGM/VTSM, RSM, SetSTN, SetHL_BTN, and
  every set op with libdvdnav clamping.
- **Random:** rnd n → 1..n.
- **Playback:** cell commands, cell and PGC stills, GoUp/Next/Prev, menu-entry lookup by type,
  and resume points.

**Host contract.** The VM never calls TTS:
- `host.play(cue, cell, token)` → play the cue, then call `vm:cueEnded(token)` after its duration
- `host.buttons(list, hl)` / `host.highlight(n)` → draw or clear the button layer
- `host.wait(sec, fn)` / `host.cancel(h)` → still timers (255 = wait for input)
- `host.subpicture(stream, on)` → SPRM2 changes (24's miss counters)
- input: `vm:press("up|down|left|right|ok")` and `vm:click(n)`. An auto button fires on highlight.

**Tests** (stock Lua 5.2, same API as MoonSharp):
- `test_dvdvm.lua` runs scripted paths. 24: boot → Extended Play code 5646766 → agent → first
  random challenge. Amazing Race: boot → full setup → route → race start.
- `fuzz_dvdvm.lua` is a random-input soak test (200–300k steps per seed). It found two real
  disc bugs and one VM bug (GoUp to PGC 0, now ignored).
- A 300-run route-generation check found no invalid cities through normal setup.

---------------------------------------------------------------------------
## 4. Issues the VM surfaced (now in the Disc Logic docs)

- **24 v1m53:** one of the 24 four-agent turn orders has no post commands, so the disc dead-ends
  at Mission Briefing about 1 game in 24.
- **Amazing Race QC quick-start:** VMGM4/5's hidden buttons skip the setup's register reset.
  After a game, this can generate an invalid city and land on a QC crash still.
  The TTS button layer should not expose those three buttons.

---------------------------------------------------------------------------
## 5. The TTS host (built: `dvdgame_vm_host.lua` + `build_tts_global.py` -> `<name>_global.lua`)

1. **Load.** Fetch `<name>_lua.json`, build cueName → effect index, then fetch
   `<name>_program.txt` (plus fixes) as plain text into `DVDVM.new`.
2. **Play: preroll with overlap.** This was measured in TTS with the SeamTest kit
   (`SeamTest\README.md`), with the screen recorded and the audio listened to on a
   continuous test tone.
   - **What TTS does:**
     - The single-screen engine stalls the picture at every switch.
     - Unity cuts about the last 200 ms of every clip's audio, and clips the first few
       tens of ms. Audio after the start stays in sync.
     - Audio that runs past the end of a clip's video never plays.
     - A cue starting on one object doesn't disturb the other object's sound.
     - `warm` and `hold` can't work with one bundle: the waiting player draws over the
       shared RenderTexture.
   - **What works (no Unity changes):**
     - Two Custom AssetBundle objects load the *same* bundle. Cells alternate between
       them, so the next cell prepares on the idle object while the current one plays to
       its end. A preparing player draws nothing (Wait For First Frame), so the shared
       texture holds the outgoing picture until the incoming frame 0 lands.
     - Each cell is started one duration after the previous one. Measured Lua timer
       error is +1.5 to +6.5 ms per switch, under a fifth of a video frame.
   - **Status (24 in TTS): not needed.** With the dumper's tail-audio fix (`dvd_cells.py`
     reads past the cell for its own last ~200 ms of audio), 24 plays seamlessly in TTS with
     preroll alone. The gap on real content was the dumper's silence; Unity's own cut only
     showed on the synthetic tone. The overlap below stays documented, and the host keeps a
     per-cue `chained` flag with `OVERLAP = 0`, in case another disc needs it.
   - **The audio fix is the overlap.** Every clip whose next cell is fixed carries that
     cell's first **7 frames (233 ms)**, picture and sound, and the host starts the next
     cell 233 ms early.
     - On screen, both players draw identical images during the overlap, so the picture
       can't flicker.
     - The outgoing clip plays the stretch of audio Unity cuts from it and clips from
       the incoming cell.
     - Tested at 3, 6, 7, 8 and 10 frames: 7 was the shortest with no gap. 10 was
       audibly doubled on the test tone.
   - **How often the successor is fixed** (a cell with no still and no cell command,
     followed by another cell in the same PGC):

     | Disc | Cues | One fixed successor | Two possible successors | Never followed by another cell |
     |---|---|---|---|---|
     | 24 | 1,239 | 631 | 6 | 602 |
     | Amazing Race | 2,432 | 1,100 | 10 | 1,322 |

     - No cue is chained in one PGC and ends a PGC in another.
     - A cue with two possible successors gets one clip per successor (`<cue>><next>`).
     - Cells that end in a still, a cell command or the end of a PGC get 7 frames of
       their own last frame and silence, so the cut eats padding instead of real audio.
     - The cost is about 7 frames on roughly half of all cells, around 1% more video.
   - **Encode:** `dvd_cells.py` appends the overlap or the padding, and the manifest
     keeps the cell's own length as `duration`.
   - **Host:** the next cue is started at `duration − 0.2336 s` after the current one on
     a chained join. After any other end it's started at `duration`, and the padding
     covers the cut.
     - Button presses and mispredictions start the target cue at once on the idle
       object. That's a short, audible join, much as a real player gives on a jump.
   - **Caveat:** the 200 ms cut was measured on one PC. Other TTS clients decode the
     video locally, so the 7-frame value may need a frame of margin once others test it.
3. **Buttons.** A transparent UI panel over the screen, plus one invisible rect per button
   (720×480 → panel coordinates). Clicking calls `vm:click(n)`, and a D-pad + OK bar calls
   `vm:press`.
   - Highlight outlines must be optional per cell: 24's ammo counter is six stacked invisible
     buttons, and drawing them would spoil the shooting games.
   - The cleanest source for which buttons are visible is the PCI button colour/contrast. That
     means a small addition to `dvd_program.py`, which already reads one NAV pack per cell.
4. **Subpictures and button highlights.** `dvd_spu_export.py` writes `<name>_spu.txt` plus `SPU\`:
   - `SPU\layers\`: the raw 2-bit layers (24: 95, Amazing Race: 45), for hosts that recolour
     at runtime. The test player does this.
   - `SPU\img\`: pre-rendered RGBA PNGs for TTS (24: 2,998 / 840 KB; Amazing Race: 504 / 332 KB).
     These are every button's selected and activated look, plus 24's always-visible ammo rows.
     The `H` / `N` records say where each image sits on the 720×480 frame.
   - The VM reports the active physical stream at every cell start and after SetSTN, using
     libdvdnav's rules and the PGC's stream map (now in the program file). It also signals
     `host.activated(n)` just before a button's command runs, for the activation colours.
   - In TTS, the highlight is a UI image swapped on `host.highlight` / `host.activated`; base
     layers show while the cell plays, until their `stop` time (24's shoot-out stage ends).
5. **No save/load.** A DVD resets every time it's ejected. On a TTS load, the disc starts again
   from First Play: there's no restored mid-game state, and no risk of a half-restored one.
6. **Multiplayer.** Only Global runs the VM; any player's input routes to it.

**Open decisions for you:**
- Ship bug-for-bug, or with the fix files?
- Should arrow keys be exposed at all on 24's TT16? Its arrow-only buttons are traps.
