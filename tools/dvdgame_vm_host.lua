-- ==========================================================================
-- %%TITLE%% -- TTS Global script (DVD VM port)
-- Built by build_tts_global.py on %%DATE%% from the disc's own navigation program.
-- Do not edit the data blocks at the bottom by hand: rebuild instead.
--
-- Objects (Custom AssetBundle):
--   SCREEN_A, SCREEN_B   the video bundle, twice. Cells alternate between them on
--                        natural cell ends (preroll), so the next cell prepares
--                        while the current one plays out. B is snapped onto A.
--   HIGHLIGHT            the subpicture bundle: the highlighted button's look
--   BASE                 the subpicture bundle again: always-visible layers
--                        (leave "" for a disc that has none)
-- All of them are locked, made non-interactable and snapped onto SCREEN_A.
--
-- Fill in the four GUIDs below, Save & Play. The disc starts by itself, the way
-- a DVD does when inserted; "Eject" restarts it from First Play.
--
-- Controls: click the buttons on the picture, or the remote panel (arrows, OK).
-- Hotkeys (Options > Game Keys): DVD Up / Down / Left / Right / OK.
--
-- Console:
--   lua uiCalibrate()     alignment chart + the click layer's outline; adjust
--                         FRAME (lua setFrame(cx, cy, w, h)) until the red outline
--                         sits on the chart's red border
--   lua uiDebug(true)     tint every clickable button rect (false to hide)
--   lua dvdState()        VM position and registers
--   lua dvdTrace(true)    log every cue / link to the console
--   lua resetScreens()    everything to Idle/None -- run before saving
-- ==========================================================================

local CONFIG = {
  SCREEN_A  = "PASTE_SCREEN_A_GUID",
  SCREEN_B  = "PASTE_SCREEN_B_GUID",
  HIGHLIGHT = "PASTE_HIGHLIGHT_GUID",
  BASE      = "%%BASE_DEFAULT%%",
  FIXES     = true,        -- apply the disc-bug patches (see the Disc Logic doc)
  OVERLAP   = 0,           -- seconds a chained cue starts early (0 = clips cut exactly)
  RETRIGGER_FRAMES = 3,    -- replaying the cue an object is already on: Idle first, then the
                           -- cue this many frames later, so other players see a change too
}

-- Click layer geometry, on SCREEN_A's object UI (same approach as the Khufu port).
-- FRAME = where the 4:3 picture sits on the 1000x1000 panel: centre x/y, width, height.
-- Calibrate once with lua uiCalibrate().
local UI_PANEL_ROTATION = "0 -90 90"
local UI_SCALE = 0.1
local UI_Z = -10
local FRAME = { cx = 0, cy = 10, w = 1333, h = 1000 }

-- ---- data (filled in by build_tts_global.py) -------------------------------
local PROGRAM, FIXES, CUES, SPU          -- long strings, defined at the end of the file

-- ==========================================================================
-- DVD VM (dvdvm.lua, embedded unchanged)
-- ==========================================================================
local DVDVM = (function()
%%DVDVM%%
end)()

-- ==========================================================================
-- Host
-- ==========================================================================
local OBJ = {}          -- A, B, HL, BASE = {name, obj, idx}
local cue = {}          -- cueName -> {dur, loop, chained}
local spuH = {}         -- cue -> raw H line, parsed on first use into [stream][btn] = {sel, act}
local spuN = {}         -- cue -> raw N line, parsed into [stream] = {img, stop}
local vm
local trace, uiDebugOn = false, false
local front             -- the video object the current cue is on
local curCue
local endTimer, baseTimer
local natural = 0       -- > 0 while running a natural cell end (cue ended / still elapsed)
local buttons, hl       -- current cell's buttons and highlighted button
local stream            -- active subpicture stream
local clickMap = {}

local function say(m) print("[dvd] " .. m) end
local function log(m) if trace then say(m) end end

local function split(s)
  local t = {}
  for w in string.gmatch(s, "%S+") do t[#t + 1] = w end
  return t
end

-- -- objects / effects --------------------------------------------------------

local function effectIndex(o, name)
  if not o.idx then
    local list = o.obj.AssetBundle.getLoopingEffects()
    if not list or #list == 0 then return nil end
    o.idx = {}
    for _, e in ipairs(list) do o.idx[e.name] = e.index end
  end
  return o.idx[name]
end

-- o.cur = the effect the object is on. Multiplayer: the host's playLoopingEffect is sent to
-- the other players, and asking an object for the effect it already shows restarts it on
-- the host only -- everyone else sees no change. Video cues therefore never repeat on the
-- same object directly (see startCue); overlays just skip the no-op.
local function playOn(o, name)
  if not o then return false end
  if o.overlay and o.cur == name then return true end
  local i = effectIndex(o, name)
  if not i then
    say(o.name .. ": no effect '" .. tostring(name) .. "'")
    return false
  end
  o.obj.AssetBundle.playLoopingEffect(i)
  o.cur = name
  return true
end

local pendingStart                          -- a delayed cue start (re-trigger), cancellable

-- Start cue c on object o, then call started(). If o is already on c, switch it to Idle
-- and start c a few frames later: a clip draws nothing while it prepares, so the brief
-- Idle never reaches the screen, and every player gets a real change.
local function startCue(o, c, started)
  if pendingStart then pcall(Wait.stop, pendingStart); pendingStart = nil end
  if o.cur ~= c then
    playOn(o, c)
    started()
    return
  end
  playOn(o, "Idle")
  pendingStart = Wait.frames(function()
    pendingStart = nil
    playOn(o, c)
    started()
  end, CONFIG.RETRIGGER_FRAMES)
end

local function stopTimer(id)
  if id then pcall(Wait.stop, id) end
end

-- -- subpictures ----------------------------------------------------------------

local function hlTable(c)
  local raw = spuH[c]
  if type(raw) ~= "string" then return raw end
  local t = {}
  for _, e in ipairs(split(raw)) do                     -- s,btn,sel,act
    local s, b, sel, act = string.match(e, "^(%d+),(%d+),([%w%-]+),([%w%-]+)$")
    if s then
      s, b = tonumber(s), tonumber(b)
      t[s] = t[s] or {}
      t[s][b] = { sel ~= "-" and sel or nil, act ~= "-" and act or nil }
    end
  end
  spuH[c] = t
  return t
end

local function baseTable(c)
  local raw = spuN[c]
  if type(raw) ~= "string" then return raw end
  local t = {}
  for _, e in ipairs(split(raw)) do                     -- s,img,stop
    local s, img, stop = string.match(e, "^(%d+),(%w+),([%d%.%-]+)$")
    if s then t[tonumber(s)] = { img = img, stop = tonumber(stop) } end
  end
  spuN[c] = t
  return t
end

local function showHighlight(n, activated)
  if not OBJ.HL then return end
  local img
  local t = curCue and hlTable(curCue)
  if t and n and buttons then
    local s = t[stream or 0] or t[0]
    local e = s and s[n]
    if e then img = activated and (e[2] or e[1]) or e[1] end
  end
  playOn(OBJ.HL, img and ("S_" .. img) or "None")
end

local function showBase()
  stopTimer(baseTimer); baseTimer = nil
  if not OBJ.BASE then return end
  local t = curCue and baseTable(curCue)
  local e = t and stream and t[stream]
  if not e then playOn(OBJ.BASE, "None"); return end
  playOn(OBJ.BASE, "S_" .. e.img)
  if e.stop then
    baseTimer = Wait.time(function() baseTimer = nil; playOn(OBJ.BASE, "None") end, e.stop)
  end
end

-- -- click layer --------------------------------------------------------------

local function discToPanel(x0, y0, x1, y1)
  local w, h = x1 - x0 + 1, y1 - y0 + 1
  return math.floor(FRAME.cx + ((x0 + w / 2) / 720 - 0.5) * FRAME.w),
         math.floor(FRAME.cy + (0.5 - (y0 + h / 2) / 480) * FRAME.h),
         math.floor(w / 720 * FRAME.w), math.floor(h / 480 * FRAME.h)
end

local calibrating = false

local function updateUI()
  if not OBJ.A then return end
  clickMap = {}
  local parts = {}
  for i, b in ipairs(buttons or {}) do
    local x, y, w, h = discToPanel(b.x0, b.y0, b.x1, b.y1)
    local id = "b" .. i
    clickMap[id] = b.n
    local color = uiDebugOn and ("rgba(%d,%d,%d,0.35)"):format((b.n * 97) % 256, (b.n * 57) % 256, (b.n * 151) % 256)
                  or "rgba(0,0,0,0)"
    parts[#parts + 1] = ('<Button id="%s" rectAlignment="MiddleCenter" position="%d %d %d" width="%d" height="%d" '
      .. 'color="%s" fontSize="24" onClick="Global/onDvdClick">%s</Button>')
      :format(id, x, y, UI_Z, w, h, color, uiDebugOn and tostring(b.n) or "")
  end
  if calibrating or uiDebugOn then
    parts[#parts + 1] = ('<Panel rectAlignment="MiddleCenter" position="%d %d %d" width="%d" height="%d" '
      .. 'color="rgba(1,0,0,0.08)" raycastTarget="false" outline="#FF0000" outlineSize="3 3" />')
      :format(FRAME.cx, FRAME.cy, UI_Z, FRAME.w, FRAME.h)
  end
  OBJ.A.obj.UI.setXml(#parts == 0 and "" or
    ('<Panel rectAlignment="MiddleCenter" width="1000" height="1000" rotation="%s" scale="%s %s 1">%s</Panel>')
      :format(UI_PANEL_ROTATION, UI_SCALE, UI_SCALE, table.concat(parts)))
end

function onDvdClick(player, value, id)
  local n = clickMap[id]
  if n and vm then vm:click(n) end
end

-- -- host interface for the VM ------------------------------------------------

local function other(o) return o == OBJ.A and OBJ.B or OBJ.A end

local host = {}

function host.play(c, cell, token)
  stopTimer(endTimer); endTimer = nil
  -- natural end: the next cell goes on the other screen so it prepares while this one
  -- finishes; anything else (a button, a jump) replaces the current cue at once
  local target = front or OBJ.A
  if natural > 0 and OBJ.B then target = other(front or OBJ.A) end
  front = target
  curCue = c
  log(("play %s (cell %d) on %s%s"):format(c, cell, target.name, target.cur == c and " (re-trigger)" or ""))
  local info = cue[c]
  if not info then
    say("no duration for cue " .. c .. " -- treating it as 1 s")
    info = { dur = 1 }
  end
  startCue(target, c, function()
    if info.loop then return end
    local d = info.dur - (info.chained and CONFIG.OVERLAP or 0)
    endTimer = Wait.time(function()
      endTimer = nil
      natural = natural + 1
      local ok, err = pcall(function() vm:cueEnded(token) end)
      natural = natural - 1
      if not ok then say("VM error: " .. tostring(err)) end
    end, math.max(d, 0.05))
  end)
end

function host.buttons(list, h)
  buttons, hl = list, h
  updateUI()
  showHighlight(list and h or nil)
end

function host.highlight(n)
  hl = n
  showHighlight(n)
end

function host.activated(n)
  showHighlight(n, true)
end

function host.subpicture(s, shown)
  stream = s
  showBase()
  if buttons then showHighlight(hl) end
end

function host.wait(sec, fn)
  if sec == 255 then return nil end
  return Wait.time(function()
    natural = natural + 1
    local ok, err = pcall(fn)
    natural = natural - 1
    if not ok then say("VM error: " .. tostring(err)) end
  end, sec)
end

function host.cancel(h) stopTimer(h) end

function host.random(n) return math.random(n) end

function host.log(m) log(m) end

-- -- input ---------------------------------------------------------------------

local function press(key)
  if not vm then return end
  local ok, err = pcall(function() vm:press(key) end)
  if not ok then say("VM error: " .. tostring(err)) end
end

function dvdUp() press("up") end
function dvdDown() press("down") end
function dvdLeft() press("left") end
function dvdRight() press("right") end
function dvdOk() press("ok") end

-- -- start / reset -------------------------------------------------------------

local function allIdle()
  stopTimer(endTimer); endTimer = nil
  if pendingStart then pcall(Wait.stop, pendingStart); pendingStart = nil end
  stopTimer(baseTimer); baseTimer = nil
  for _, k in ipairs({ "A", "B" }) do if OBJ[k] then playOn(OBJ[k], "Idle") end end
  for _, k in ipairs({ "HL", "BASE" }) do if OBJ[k] then playOn(OBJ[k], "None") end end
  buttons, hl, curCue, stream = nil, nil, nil, nil
  front = OBJ.A
  updateUI()
end

function resetScreens()
  vm = nil
  allIdle()
  say("all screens idle -- safe to save")
end

function dvdEject()
  allIdle()
  math.randomseed(os.time())
  vm = DVDVM.new(PROGRAM .. (CONFIG.FIXES and ("\n" .. FIXES) or ""), host)
  -- let the Idle switch land before First Play
  Wait.time(function()
    local ok, err = pcall(function() vm:start() end)
    if not ok then say("VM error: " .. tostring(err)) end
  end, 0.3)
end

function dvdState() if vm then say(vm:state()) end end
function dvdTrace(on) trace = (on ~= false); say("trace " .. tostring(trace)) end
function uiDebug(on) uiDebugOn = (on ~= false); updateUI() end

function uiCalibrate(on)
  calibrating = (on ~= false)
  if OBJ.HL then playOn(OBJ.HL, calibrating and "SPU.Calibrate" or "None") end
  updateUI()
  say(calibrating and "calibrating: lua setFrame(cx, cy, w, h) until the red outline sits on the chart's border; lua uiCalibrate(false) when done"
      or "calibration off")
end

function setFrame(cx, cy, w, h)
  local function i(v, d) return v and math.floor(v + 0.5) or d end
  FRAME.cx, FRAME.cy, FRAME.w, FRAME.h = i(cx, FRAME.cx), i(cy, FRAME.cy), i(w, FRAME.w), i(h, FRAME.h)
  updateUI()
  say(("FRAME = { cx = %d, cy = %d, w = %d, h = %d }  -- copy into the script"):format(FRAME.cx, FRAME.cy, FRAME.w, FRAME.h))
end

-- -- remote panel (Global UI) ----------------------------------------------------

local function remoteXml()
  local function b(label, fn, x, y, w)
    return ('<Button onClick="%s" position="%d %d" width="%d" height="44" fontSize="20">%s</Button>')
      :format(fn, x, y, w or 54, label)
  end
  return table.concat({
    '<Panel id="dvdRemote" width="200" height="190" rectAlignment="LowerRight" offsetXY="-20 80"',
    ' color="rgba(0,0,0,0.6)" allowDragging="true" returnToOriginalPositionWhenReleased="false">',
    b("^", "dvdUp", 0, 50), b("&lt;", "dvdLeft", -60, 0), b("OK", "dvdOk", 0, 0), b("&gt;", "dvdRight", 60, 0),
    b("v", "dvdDown", 0, -50), b("Eject", "dvdEject", 0, -110, 120),
    '</Panel>',
  })
end

-- -- load ------------------------------------------------------------------------

local function loadData()
  for line in string.gmatch(CUES, "[^\n]+") do        -- name dur loop chained
    local f = split(line)
    if f[2] then cue[f[1]] = { dur = tonumber(f[2]), loop = f[3] == "1", chained = f[4] == "1" } end
  end
  for line in string.gmatch(SPU, "[^\n]+") do         -- H/N <cue> entries...
    local kind, c, rest = string.match(line, "^(%a) (%S+) (.*)$")
    if kind == "H" then spuH[c] = rest elseif kind == "N" then spuN[c] = rest end
  end
end

local function grab(key, guid)
  if not guid or guid == "" then return nil end
  local o = getObjectFromGUID(guid)
  if not o then
    if not string.find(guid, "PASTE") then say(key .. ": no object with GUID " .. guid) end
    return nil
  end
  o.setLock(true)
  o.interactable = false
  return { name = key, obj = o }
end

local function snap(o)
  if not o or not OBJ.A then return end
  o.obj.setPosition(OBJ.A.obj.getPosition())
  o.obj.setRotation(OBJ.A.obj.getRotation())
  o.obj.setScale(OBJ.A.obj.getScale())
end

function onLoad()
  loadData()
  UI.setXml(remoteXml())
  for _, k in ipairs({ "Up", "Down", "Left", "Right", "OK" }) do
    addHotkey("DVD " .. k, function() press(string.lower(k)) end)
  end
  OBJ.A = grab("A", CONFIG.SCREEN_A)
  OBJ.B = grab("B", CONFIG.SCREEN_B)
  OBJ.HL = grab("HL", CONFIG.HIGHLIGHT)
  OBJ.BASE = grab("BASE", CONFIG.BASE)
  if OBJ.HL then OBJ.HL.overlay = true end
  if OBJ.BASE then OBJ.BASE.overlay = true end
  if not OBJ.A then say("set CONFIG.SCREEN_A (and the others) to the objects' GUIDs"); return end
  snap(OBJ.B); snap(OBJ.HL); snap(OBJ.BASE)
  local function ready()
    for _, k in ipairs({ "A", "B", "HL", "BASE" }) do
      local o = OBJ[k]
      if o then
        local probe = (k == "A" or k == "B") and "Idle" or "None"
        if not effectIndex(o, probe) then return false end
      end
    end
    return true
  end
  say("waiting for the bundles...")
  Wait.condition(function()
    say(("ready: %d video effects, %s subpicture effects"):format(
      #OBJ.A.obj.AssetBundle.getLoopingEffects(),
      OBJ.HL and tostring(#OBJ.HL.obj.AssetBundle.getLoopingEffects()) or "no"))
    dvdEject()
  end, ready, 180, function() say("bundles didn't report their effects within 180 s") end)
end

-- ==========================================================================
-- Data (generated)
-- ==========================================================================
PROGRAM = [=====[
%%PROGRAM%%
]=====]

FIXES = [=====[
%%FIXES%%
]=====]

-- name duration loop(1/0) chained(1/0)
CUES = [=====[
%%CUES%%
]=====]

-- H <cue> stream,button,select,activate ...   N <cue> stream,image,stop ...
SPU = [=====[
%%SPU%%
]=====]
