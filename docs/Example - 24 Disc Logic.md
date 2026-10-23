# 24: The DVD Board Game — disc logic (reversed)

Source: IFOs + PgcEdit pseudocode/button dumps in `24 The DVD Board Game\`.
Every PGC was decoded (573/573 match the IFOs). The full decoded listing is
`24 - decoded listing.txt`, with a shape-compressed copy alongside; this file is the readable layout.
Names: `VMGM36` = VMG menu PGC 36, `v1m5` = VTS 1 menu PGC 5, `TT7` = title 7,
`v7p11` = VTS 7 title PGC 11. Every title VTS holds exactly one title, so **TTn = VTSn**.

Content was identified from one frame per cell (full-res, with the button
rectangles drawn on), so descriptions are from stills, not from watching the clips.

---------------------------------------------------------------------------
## 1. Disc start and menus

FP -> VMGM2: clears g0–g15 (g6 = 101, g11 = 2300), plays Warning, Pressman,
20th Century Fox TV, 24 logo (button skips) -> VTSM1 "Subpicture" menu
(v1m2: 5 s intro, then the main menu). VMGM1 / VMGM72 do the same reset without the logos;
"CallSS VMGM Title" at the end of a day, and "Yes" on the quit screen, land there.

**Main menu (v1m2 / v1m3)**
| Button | Goes to |
|---|---|
| Play | v1m4 -> v1m5 Day Selection |
| How To Play | v1m75/78: Play All / Setup / How To Play / How To Win / Extended Play (v1m89–93) |
| B1 Games | v1m76/80: Trailers (v1m82/84 -> TT1, 4 trailers picked by highlighted button) / Contact |
| B1 Extras | v1m77/79: DVD-ROM / Legal |
| disc icon (bottom left) | VMGM10 -> TT21 = disc credits -> back to the main menu (g15 = 11380 is the "came back from credits" token) |

---------------------------------------------------------------------------
## 2. Setup (VTSM1)

1. **Day Selection (v1m5 cell 1)**: Day 1 / Day 2 / Day 3 -> g5 = 1/2/3, or
   **Extended Play** -> pass-code pad.
2. **Extended Play pass code** (v1m5 cells 2–8, one cell per digit, keypad 1–9):
   **5 6 4 6 7 6 6**. Each day's win screen gives a piece: Day 1 = `564`,
   Day 2 = `67`, Day 3 = `66`. Correct -> "accepted" -> g5 = 4.
   One wrong digit -> "invalid" cell, sets g14 = 1138, and from then on Play goes to
   **v1m6, a Day Selection without the Extended Play button**, until the disc is reset
   (quit, or a finished day). One mistake locks you out for the session.
3. **Agent Selection (v1m7)**: Moray (yellow), Scapa (blue), Landy (red), Parker (green).
   Each pick has a confirm screen (v1m8–11); an agent already taken shows v1m13 and
   returns. Temporary flags g7–g10 = 1 mark who's taken, g0 counts them.
   After the first pick the menu gains **"No other agent needed"** (one agent is allowed).
   After a 3rd pick the 4th agent is added automatically (v1m14–17).
4. **Agent Order** (random turn order, normal days only):
   - 2 agents: v1m18, rnd(6) > 3 -> reversed order (50/50), screens v1m21–26.
   - 3 agents: v1m19 picks the trio by who's missing -> v1m27/33/39/45, each meant to
     pick 1 of 6 orders (**bug, §8.1: always the same order**).
   - 4 agents: v1m51, rnd(65530) % 24 + 1 -> one of 24 order screens v1m51–74.
   Each screen writes the order into g1–g4, then "Mission Briefing" -> VMGM3.
   Extended Play skips the order screens: slots are filled in pick order.
5. **VMGM3**: clears the temporary flags g7–g10, g12 = 1 (first agent), then by g5:
   Day 1 -> VTSM2 intro, Day 2 -> VTSM6 intro, Day 3 -> VTSM13 intro, Extended -> VMGM84.

---------------------------------------------------------------------------
## 3. Registers

| Reg | Meaning |
|---|---|
| g0 | number of agents (1–4) |
| g1–g4 | agent slots in turn order. Bits 12–14 = agent: 4 Moray/yellow (16384), 5 Scapa/blue (20480), 6 Landy/red (24576), 7 Parker/green (28672) |
| g5 | 1–3 = day being played, 4 = Extended Play |
| g6 | **story step**. `1xx` = step xx is unlocked; `2xx` = step xx started (replays a short recap instead of the full intro if you fail and come back); `11xx` = special hand-off steps (1112, 1120, 1121). Locations test `g6 % 100` |
| g7 | setup: Moray flag. In play: **event/clue bitmask**, one bit per pending location event (§5–7) |
| g8 | setup: Scapa flag. In play: "intro already seen" bits for event VTSs (an Extended Play challenge sets it to all-ones, so intros are skipped) |
| g9 | setup: Landy flag. In play: "this answer was already given" bits in interview menus. Extended: the challenge dice |
| g10 | setup: Parker flag. Day 1: 1138 = Hospital puzzle done (Hospital then shows the code hint). Day 2 finale: alarm flags |
| g11 | **mission clock**: hour × 100 + turn counter (starts 2300) |
| g12 | whose turn (slot 1..g0) |
| g13 | turns since the last free clue |
| g14 | scratch: pass-code lockout (1138), bomb-wire state, temp copies of g7 |
| g15 | scratch, and the **parameter to event/clue titles** (which clue to show) |

---------------------------------------------------------------------------
## 4. The turn loop (normal days)

**Day intro** (VTSM2/6/13 Angle menu, ~1 min video): sets g6 (Day 1/2: 101; Day 3: 103 and g7 = 1),
g11 = 2300, then the first agent's location menu.

**Location menu** (Day 1 VMGM5–8, Day 2 VMGM26–29, Day 3 VMGM46–49; one per agent colour,
chosen by VMGM4/25/45 from the slot in g12). A "LOS ANGELES – COUNTER TERRORIST UNIT / AGENT <NAME>"
screen with 12 locations, a "Target Location" map panel, X (quit, VMGM73) and ? (help, VMGM74).
The player picks where their pawn is on the board.

| # | Day 1 (VMGM13–24) | Day 2 (VMGM33–44) | Day 3 (VMGM53–64) |
|---|---|---|---|
| 1 | CTU | CTU | CTU |
| 2 | Airport | Airport | Apartment |
| 3 | Auto Repair | Apartment | City Streets |
| 4 | City Streets | City Streets | Couriers |
| 5 | Harbor | Hospital | District |
| 6 | Hospital | Industrial Park | Gerard Hotel |
| 7 | Industrial Park | Military Base | Harbor |
| 8 | Ion Systems | Office Park | Hollywood Residence |
| 9 | OmniDyne Laboratories | Safehouse | Hospital |
| 10 | Parking Structure | Subway | Office Park |
| 11 | Train Station | Warwick's Art Gallery | Train Station |
| 12 | Warehouse District | Water Reclamation | Warehouse |

**Each location PGC** plays a 4–9 s "arriving at <location>" clip, then checks in order:
1. `g6 % 100 ==` this location's story step -> jump to the day's story title (§5–7);
2. each g7 bit tied to this location -> set g15 = clue/event id, jump to the clue or event title;
3. otherwise nothing happens -> VMGM9.

Story, clue and event titles all return with `CallSS VMGM9`.

**VMGM9, end of turn:**
```
g11 += 1
if (g11 % 100) % 4 == 0      -> VMGM68 clock tick (every 4th turn)
g13 += 1
if g13 > 5                   -> VMGM12 free clue (every 6th turn)
g12 = next agent (wraps)
-> that day's location menu
```

**VMGM68, clock tick:** shows a countdown clock still. The hour (g11 / 100) steps
23 -> 21 -> 20 -> 18 -> 15 -> 13 -> 11 -> 9 -> 8 -> 6 -> 5 -> 3 -> 2 -> 1 -> 0 (14 ticks).
The 15th tick, at hour 0, ends the day, so a day lasts about 60 agent turns (free-clue turns
excluded). The losing endings are:
VMGM69 (Day 1, submarine), VMGM70 (Day 2, bio lab, "00:00:09"), VMGM71 (Day 3, explosion, "00:00:07")
-> VMGM1 full reset.

**VMGM12, free clue:** "Agent <X> — You have found a new clue. Take a clue card." A random agent
(rnd(65500) % g0 + 1) gets it. g11 -= 1, so the clue turn doesn't advance the clock, and g13 = 0.
It is skipped with one agent (but still resets g13).

**Quit (VMGM73)**: Yes -> VMGM72 reset; No -> back to the current agent's menu.
**Help (VMGM74)**: the same How-To-Play chapters, then back to the current agent's menu.

---------------------------------------------------------------------------
## 5. Day 1 (story TT2–TT3, clues TT4, events TT5)

**Story chain** (location: step -> what plays -> result):

| Step | Location | Title / challenge | On success |
|---|---|---|---|
| 101 | OmniDyne Labs | TT2 v2p1 intro -> v2p3 "CTU uploading OmniDyne info" floor-plan tracker (3 options per beat, AUTO time-outs) | g6 = 103, g7 = 3 |
| 103/203 | CTU | v2p4 -> v2p6–9 keyboard: fill in **S_BM_R_N_** = U, A, I, E (11 s each) | 105, g7 += 12 |
| 105/205 | Industrial Park | v2p12 -> v2p14 "CTU accessing industrial warehouse": timed overhead-map tracker, 3 rounds | 107, g7 += 64 |
| 107 | Ion Systems | v2p15–19 "Ion Systems" letter-grid login | v2p20 sets 108 |
| 108 | City Streets | v2p21 -> v2p23 PDA "CTU-Incoming" target grid, 3 rounds | 109, clears bits 4 and 8, +128 |
| 109/209 | Parking Structure | TT3 v3p1/2 garage search (press when the target shows) | v3p4: 110 |
| 110/210 | Hospital | v3p5 -> v3p7 drug dial (Meperidine ↔ Epinephrine): the correct button is 12 | 113, g7 += 768, g10 = 1138 |
| 113/213 | Harbor | v3p8 bomb: cut wire 6, then 3, 1, 2 (v3p10 checks the order with g14) | v3p11 -> 214 |
| 214 | (same visit) | v3p14–18 server screen: type the repeated string **4T36N**; then "Enter Screen Code": **RHH999** or **VLD603** (v3p20 accepts either first letter, then v3p22/23) | v3p27: "Day 1 portion of the pass code: **564**" -> title menu (win) |

Failing a challenge returns to VMGM9 with g6 at 2xx, so a later visit to the same
location replays the short recap and retries.
After the Hospital puzzle, g10 = 1138, so later Hospital visits jump to VTSM3, a still
showing **"RHH"**: the hint for the first half of the screen code.

**Clue/event bits (g7)**

| Bit | Location | Opens | Set by | Cleared by |
|---|---|---|---|---|
| 1 | Warehouse District | TT5 v5p3 27-cell timing sequence | start (g7 = 3) | v5p3 |
| 2 | Auto Repair | TT4 clue v4p1 | start | v4p1 |
| 4 | Parking Structure | TT4 clue v4p2 "Location: Parking Structure" | CTU step | v4p2, or expires at step 108 |
| 8 | Airport | TT5 v5p4 interview, 5 questions, ask 3; two answers add bits 16+32768 and 32+16384 | CTU step | once both are found, or expires at step 108 |
| 16 | Harbor | TT4 clue v4p3 "Location: Harbor" | Airport interview | v4p3 |
| 32 | CTU | TT4 clue v4p4 "Location: CTU" | Airport interview | v4p4 |
| 64 | Train Station | TT5 v5p13/15 rail-yard chase (rnd(3) picks 1 of 3 cuts) | Industrial Park | v5p18 (success) |
| 128 | Airport | TT4 clue v4p5 "Location: Airport" | City Streets | v4p5 |
| 256 | Ion Systems | TT5 v5p20/22 keyboard **B2Y7D** | Hospital | v5p28 |
| 512 | OmniDyne | TT5 v5p29/31 keypad (24-key) | Hospital | v5p36 |

---------------------------------------------------------------------------
## 6. Day 2 (story TT6–TT7, finale TT8, clues TT9, events TT10–12)

| Step | Location | Title / challenge | On success |
|---|---|---|---|
| 101 | Military Base | TT6 interview: 7 questions, ask 3. Question 5 (v6p5) moves the story; q4 adds bit 1, q7 adds bit 2 | 104 (else stays 201 and you return) |
| 104/204 | Warwick's Art Gallery | v6p12–14, v6p30, v6p33: three press-on-cue rounds | v6p35: 105 |
| 105/205 | CTU | v6p19 -> v6p20–26 keyboard with a Morse/alphanumeric chart on screen: **S L 2 3 T 1 R** | v6p27: g7 += 32, g6 = 1112 |
| 1112 | Military Base | v6p29 cutscene | 108, clears 128, +24 |
| 108/208 | Office Park | TT7 v7p3 chase -> v7p6 choice (button 3 = correct, "777") -> v7p8 | 110, +256 |
| 110/210 | Industrial Park | v7p11 long sequence with 3 press points | 1120, clears bits 2/16/32768, +512 |
| 1120 | CTU | v7p12 | 111 |
| 111/211 | Safehouse | v7p13 interrogation: 6 questions, ask 3. Button 3 (v7p20) moves the story; 4 -> +8192, 2 -> +2048, 5 -> +1024 | 114 |
| 114/214 | Airport | TT8 warehouse shoot-out (below) | v8p10 -> v8p13: "Day 2 portion: **67**" -> title menu |

**TT8 shoot-out:** four stages (v8p3/5/7/9). Each is a run of 2-s cells with ~20 hotspots;
one hotspot is the gunman (next stage). Any other press is a miss: g14 counts misses and is
shown as a subpicture stream (SetSTN subp = g14); 5 misses -> v8p12 -> back to the turn.
Stage 1 also has two red **alarm boxes** on the walls (buttons 19/20): hitting both
(g10 bits 1 + 2) -> v8p11, an alternate ending that goes straight to the title menu
**without the pass-code screen**.

**Clue/event bits (g7)**

| Bit | Location | Opens |
|---|---|---|
| 1 | Subway | TT9 clue (g15 = 3) |
| 2 | Hospital | TT10 event (g15 = 2); success does g7 += 2, carrying into bit 4 (§8.6) |
| 4 | Apartment | TT10 event (g15 = 3), clears 4 |
| 8 | Airport | TT11 parking-garage shooting gallery, 3 rounds (§8.5 mechanic), clears 8 |
| 16 | CTU | TT9 clue 13 |
| 32 | Water Reclamation | TT10 event (g15 = 6), 3 parts; success sets 32768 + 64 (+128 if bit 8 is gone) |
| 64 | Subway | TT9 clue 11 |
| 128 | Airport | TT9 clue 9 (checked after bit 8) |
| 256 | Warwick's Art Gallery | TT9 clue 17 |
| 512 | Hospital | TT9 clue 19 |
| 1024 | Office Park | TT9 clue 22 |
| 2048 | Subway | TT12 event (g15 = 13), success -> bit 4096 |
| 4096 | City Streets | TT9 clue 26 |
| 8192 | CTU | TT12 event (g15 = 12), 6-way choice, success -> bit 16384 |
| 16384 | Hospital | TT9 clue 24 |
| 32768 | Hospital | TT9 clue 10 |

---------------------------------------------------------------------------
## 7. Day 3 (story TT13–TT15, finale TT16, clues TT17, events TT18–20, TT22, VTSM19)

The intro sets g6 = 103 and g7 = 1.

| Step | Location | Title / challenge | On success |
|---|---|---|---|
| 103/203 | Warehouse | TT13 keyboard: **C_RR_PT__N** = O, U, I, O | v13p8: 105, +48 |
| 105/205 | Hollywood Residence | v13p11 interrogation: 5 questions, ask 2. Button 2 (v13p15) moves the story; 4 -> +128; 3 -> **g7 = 64** (§8.4) | 106 |
| 106/206 | Office Park | TT14 rooftop sniper: one shot only (g14 > 1 fails); rnd(2) picks 1 of 2 layouts | v14p6: 108, clears 2, +512 |
| 108/208 | CTU | TT15 menu, 5 info buttons: 5 -> v15p8 (111); 3 -> +1024 | 111 |
| 111/211 | Couriers | v15p12 -> p15 -> p18: three press-on-cue rounds | v15p20: 1121 |
| 1121 | City Streets | v15p21 | 112 |
| 112/212 | District | TT16 finale: corridor shoot-out stages v16p3/5/7 (§8.5) | v16p11 -> v16p13: "Day 3 portion: **66**" -> title menu |

TT16 failure -> v16p10, back to the turn. Arrowing away from the counter onto the
trap buttons -> v16p12 explosion -> title menu (game over).

**Clue/event bits (g7)**

| Bit | Location | Opens |
|---|---|---|
| 1 | Apartment | TT18 interview: 6 questions, ask 3. Bit 1 is cleared only when all three useful answers (g9 = 7) were given; they add bits 4, 8, 2 |
| 2 | Train Station | TT17 clue 3 |
| 4 | Hospital | TT17 clue 4 |
| 8 | Harbor | TT18 event (g15 = 2), 3 stages; never cleared by TT18 itself, only by TT22 |
| 16 | Hospital | TT17 clue 7 |
| 32 | Harbor | VTSM19 -> TT19 one-shot sniper, clears 32 |
| 64 | City Streets | TT17 clue 11 |
| 128 | District | TT17 clue 12 |
| 256 | CTU | TT17 clue 18 |
| 512 | Train Station | TT20 event (g15 = 7), 3 stages, clears 512 |
| 1024 | Gerard Hotel | TT20 event (g15 = 9), long chain, -> bit 4096 |
| 2048 | Harbor | TT17 clue 19 |
| 4096 | Warehouse | TT22 event: clears 8/32/4096, adds 256 + 2048 |

---------------------------------------------------------------------------
## 8. Extended Play (g5 = 4)

No story, no clock. The disc becomes a random-challenge dispenser for the board game:

**VMGM84** advances g12 and shows **VMGM80–83** "Agent <X> — Play a Challenge / Arrived at CTU / X".
- **Play a Challenge**: g9 = rnd(3) picks the day pool, then a second roll picks a challenge.
  It enters that VTS's PTT menu, which presets g6/g15/g8 so the challenge starts directly:

  | Pool | Roll | Challenge (g6 / g15 preset) |
  |---|---|---|
  | Day 1 | rnd(10) 1–5 (50%) | VTSM2: 1 OmniDyne tracker (101), 2 SUBMARINE (203), 3 warehouse tracker (205), 4 Ion grid (107), 5 PDA grid (108) |
  | | 6 (10%) | VTSM3: parking-garage search (209) |
  | | 7–10 (40%) | VTSM5: 7 warehouse timing, 8 rail-yard chase, 9 B2Y7D keyboard, 10 OmniDyne keypad |
  | Day 2 | rnd(10) 1–3 | VTSM6: Warwick's rounds (204) / CTU Morse keyboard (205) |
  | | 4–5 | VTSM7: Office Park chase (208) / Industrial Park (210) |
  | | 6–7 | VTSM10: Hospital/Apartment events |
  | | 8 | VTSM11: garage shooting gallery |
  | | 9–10 | VTSM12: CTU / Subway events |
  | Day 3 | rnd(9) 1 | VTSM13: CORRUPTION keyboard |
  | | 2 | VTSM14: rooftop sniper |
  | | 3–4 | VTSM15: CTU menu (208) / Couriers (211) |
  | | 5 | VTSM18: Apartment interview |
  | | 6 | VTSM19: Harbor sniper |
  | | 7–8 | VTSM20: Train Station / Gerard Hotel |
  | | 9 | VTSM22: Warehouse event |

  The day finales (TT8, TT16) and clue screens are never offered.
  Every challenge ends with `CallSS VMGM9`, and VMGM9 sends g5 = 4 straight to VMGM84: next agent.
- **Arrived at CTU** -> VMGM85: rnd(2) plays one of two 6.7-s ending clips (VMGM85 / VMGM86) -> VMGM1 reset.
- **X** -> quit screen.

---------------------------------------------------------------------------
## 9. Shared mechanics

1. **Timing/direction games** use auto-action buttons. An auto-action button fires the moment it
   becomes highlighted, so with the button's up/down/left/right links (`udlr` in the dump) an
   **arrow key press is an instant action**. Example, v2p3 cell 1: btn1 (Enter) links back to
   the start, and its udlr is {1 1 3 2}. Left highlights btn3 (auto -> LinkCN 2) and right
   highlights btn2 (auto -> LinkCN 3), so "track the dot" means pressing the right arrow at the
   right time. The time-outs are the cell commands at the end of each cell. Buttons also have
   per-cell highlight windows (hlstarttime/hlendtime in the dump).
2. **Keyboard puzzles** use one 31-button map across the disc:
   buttons 1–10 = `1 2 3 4 5 6 7 8 9 0`, 11–19 = `W E R T Y U I O P`, 20–25 = `A S D F G H`, 26 = `L`,
   27–31 = `C V B N M`. Q, J, K, Z, X can't be typed. Each correct key -> the next PGC;
   any other key -> fail.
3. **Interview menus** count questions in g15 (3 per visit, 2 on Day 3) and use g9/g7 bits so each
   answer only pays out once.
4. **Crosshair shooting** (TT11, TT14, TT16, TT19) uses the *highlighted button as an ammo counter*:
   buttons 1–6 are stacked invisible rectangles. On an empty frame each does
   `LinkNextPG (button n+1)`, so a miss moves the highlight to the next button. Button 6 = out of shots
   -> fail. On a target frame, every counter button links to the hit PGC. A grid of extra buttons
   7–30 is only reachable with the arrow keys; on TT16 some of them trigger the bomb ending, so
   remote-mashing arrows is punished.
5. **Miss counters** (TT8, TT14, TT19) are drawn by switching subpicture streams (SetSTN subp = g14).

---------------------------------------------------------------------------
## 10. Disc bugs / quirks found

1. **3-agent turn order is fixed.** v1m27/33/39/45 do `g15 = 65530` (an immediate, not `rnd`),
   then `% 6 + 1` = 5, so every trio always gets its 5th order screen (v1m31/37/43/49).
   2- and 4-agent orders are random. This is the same mistake as Amazing Race's v3p4/v3p7 (`g15 = 65535`).
2. **v7p3 rolls rnd(208)** and then tests for 2 or 3, so the Office Park chase variants v7p4 and v7p5
   play 1 time in 104 each instead of 1 in 3. Almost certainly meant rnd(3).
3. **VMGM40 (Day 2 Office Park)** tests `g15 != 9` on the already-reduced step, so step 9 would also
   enter TT7. Day 2 never uses step 9, so this is harmless.
4. **v13p16 assigns `g7 = 64`** where every other answer adds a bit. Picking that interrogation
   answer on Day 3 wipes all pending clues and events: at that point usually the Apartment interview
   (bit 1), the Hospital clue (16) and the Harbor sniper (32). v13p13/v13p16 also assign g9
   instead of OR-ing it, so the "already answered" guard is unreliable.
5. **Extended Play quit -> "No" skips a turn.** VMGM73's "No" path goes to VMGM84, which advances g12.
6. **Bit-carry tricks** (probably intended): Day 2's Hospital event succeeds with `g7 += 2` while
   bit 2 is still set. The carry clears bit 2 and sets bit 4, moving the event to the Apartment.
   Day 1's City Streets step and Day 2's Industrial Park step silently expire unclaimed events
   (`g7 &= mask`).
7. **Day 3 Harbor** checks bit 8 (TT18 event) before bits 32 and 2048. TT18 never clears bit 8
   (only the Warehouse event TT22 does), so until then the Harbor sniper and Harbor clue are unreachable.
8. **Pass-code lockout:** a single wrong digit hides Extended Play until a reset (g14 = 1138
   survives back-outs to the menu).
9. The disc's own magic numbers 1138 / 11380 (g14 lockout, g10 Hospital flag, credits token)
   are a THX 1138 nod, as on Amazing Race (g13 = 1138 at boot).
10. **4-agent order dead end (found by the VM soak test).** v1m51 sends roll 3 to v1m53
    (Scapa, Landy, Parker, Moray). Unlike the other 23 order screens, v1m53 has **no post
    commands and no next PGC**, so "Mission Briefing" stops playback and the agent slots are never
    filled. About 1 in 24 four-agent games hangs at setup. `24_fixes.txt` gives it the same post
    commands as its siblings.

---------------------------------------------------------------------------
## 11. Open questions (needs a playthrough)

1. Where the alternate Day 1 screen code **VLD603** is shown (RHH999 is hinted at the Hospital).
2. What the two Extended Play "Arrived at CTU" endings (VMGM85/86, black video) say. They are probably
   audio-only: win/lose, or two voice variants.
3. Whether the TT8 alarm-box ending (v8p11) is a real win (it skips the pass-code screen).
4. Day 1 Ion Systems grid and OmniDyne keypad answers: they use non-keyboard layouts, so the
   correct buttons are known by number only (see the listing).
5. Whether the bit-4/8 expiry at Day 1 step 108 and the Day 3 Harbor ordering (§10.7) are intended.
