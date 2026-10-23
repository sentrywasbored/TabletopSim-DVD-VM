--[[
dvdvm.lua -- a DVD-Video navigation VM for Tabletop Simulator (MoonSharp / Lua 5.2).

Runs a disc's own navigation program (exported by dvd_program.py) instead of a
hand-port of its game logic: the VM executes the PGC pre/post/cell commands
and button commands with libdvdnav semantics, and tells the host which cue to
play and which buttons to show. Nothing here touches TTS directly, so the same
file runs headless under stock Lua 5.2 for testing (test_dvdvm.lua).

Host interface -- a table passed to DVDVM.new(programText, host):
  host.play(cue, cell)        start a cell's clip; call vm:cueEnded() when it finishes
  host.buttons(list, hl)      buttons for the cell now playing (nil = none); list[i] =
                              {n, x0, y0, x1, y1, up, down, left, right, auto, cmd}
                              in 720x480 source pixels; hl = highlighted button number
  host.highlight(n)           highlight moved (arrow keys, SetHL_BTN)
  host.wait(seconds, fn)      call fn after seconds (still cells / PGC stills); seconds may be
                              255 = infinite (don't call fn); return a handle for host.cancel
  host.cancel(handle)         cancel a pending wait
  host.subpicture(stream, shown)  the active physical subpicture stream (0..31, or nil = none)
                              at the start of every cell and after SetSTN; shown = display
                              flag (menus: always true). Forced subpictures show regardless.
  host.activated(n)           optional: button n was activated (draw its action colours);
                              called just before the button's command runs
  host.log(msg)               optional trace
  host.random(n)              optional: 1..n (default math.random)

Patches: lines "P <pgc> <pre|post|cellcmd> <index> <hex>" anywhere in the program text
replace one command (index >= 1) or a whole section (index 0, hex = concatenated
commands, "-" = empty). Append a patch file to fix disc bugs without editing the export.

Input from the host:
  vm:press("up"|"down"|"left"|"right"|"ok")   remote-control keys
  vm:click(n)                                  mouse on button n: select + activate
  vm:cueEnded(token)                           the clip passed to host.play finished;
                                               token = value host.play's caller received
]]

local DVDVM = {}
DVDVM.__index = DVDVM

-- Bit operations in plain arithmetic on unsigned 32-bit values. Not bit32: TTS's
-- MoonSharp bit32.extract rejects any field that reaches bit 31 ("Trying to access
-- Non-Existent Bits"), which is where every command's type bits are.
local P32 = 4294967296
local function extract(n, field, width)
  return math.floor((n % P32) / 2 ^ field) % 2 ^ width
end
local function bitop(a, b, keep)              -- keep(x, y) -> bit set?
  a, b = a % P32, b % P32
  local r, bit = 0, 1
  while a > 0 or b > 0 do
    local x, y = a % 2, b % 2
    if keep(x, y) then r = r + bit end
    a, b, bit = (a - x) / 2, (b - y) / 2, bit * 2
  end
  return r
end
local function band(a, b) return bitop(a, b, function(x, y) return x == 1 and y == 1 end) end
local function bor(a, b) return bitop(a, b, function(x, y) return x == 1 or y == 1 end) end
local function bxor(a, b) return bitop(a, b, function(x, y) return x ~= y end) end
local function rshift(a, n) return math.floor((a % P32) / 2 ^ n) end
local function lshift(a, n) return (a * 2 ^ n) % P32 end

local MENU_NAMES = { [2] = "Title", [3] = "Root", [4] = "Subpicture", [5] = "Audio", [6] = "Angle", [7] = "PTT" }

----------------------------------------------------------------- program file
local function split(s, sep)
  local out, i = {}, 1
  while true do
    local j = string.find(s, sep, i, true)
    if not j then out[#out + 1] = string.sub(s, i); return out end
    out[#out + 1] = string.sub(s, i, j - 1)
    i = j + #sep
  end
end

local function hexcmds(s)
  if s == "-" or s == nil then return {} end
  local out = {}
  for i = 1, #s, 16 do out[#out + 1] = string.sub(s, i, i + 15) end
  return out
end

-- Index the program text by record key; records are decoded on first use.
local function loadProgram(text)
  local p = { titles = {}, ptt = {}, raw = {}, rawButtons = {}, pgc = {}, buttons = {}, menuEntry = {} }
  for line in string.gmatch(text, "[^\n]+") do
    local kind = string.sub(line, 1, 1)
    if kind == "G" then
      local key, entry = string.match(line, "^G (%S+) (%d+)")
      p.raw[key] = line
      entry = tonumber(entry)
      if entry > 0 then
        local dom = string.match(key, "^(M%d+)%.")
        p.menuEntry[dom .. ":" .. entry] = key
      end
    elseif kind == "B" then
      local cue = string.match(line, "^B (%S+)")
      p.rawButtons[cue] = line
    elseif kind == "T" then
      local tt, vts, ttn = string.match(line, "^T (%d+) (%d+) (%d+)")
      p.titles[tonumber(tt)] = { vts = tonumber(vts), ttn = tonumber(ttn) }
    elseif kind == "R" then
      local f = split(line, " ")
      local list = {}
      for i = 4, #f do
        local pg, pn = string.match(f[i], "(%d+)%.(%d+)")
        list[#list + 1] = { pgcn = tonumber(pg), pgn = tonumber(pn) }
      end
      p.ptt[f[2] .. ":" .. f[3]] = list
    elseif kind == "P" then                 -- patch: P <pgc> <pre|post|cellcmd> <index|0=all> <hex>
      local key, sec, idx, hex = string.match(line, "^P (%S+) (%S+) (%d+) (%S+)")
      p.patches = p.patches or {}
      p.patches[key] = p.patches[key] or {}
      table.insert(p.patches[key], { sec = sec, idx = tonumber(idx), hex = hex })
    elseif kind == "D" then
      p.name = string.match(line, "^D (%S+)")
    end
  end
  return p
end

local function getPGC(p, key)
  local g = p.pgc[key]
  if g then return g end
  local line = p.raw[key]
  if not line then return nil end
  local f = split(line, " ")
  g = { key = key, entry = tonumber(f[3]), next = tonumber(f[4]), prev = tonumber(f[5]),
        goup = tonumber(f[6]), still = tonumber(f[7]), mode = tonumber(f[8]),
        progmap = {}, pre = hexcmds(f[10]), post = hexcmds(f[11]), cellcmd = hexcmds(f[12]), cells = {} }
  if f[9] ~= "-" then for _, v in ipairs(split(f[9], ",")) do g.progmap[#g.progmap + 1] = tonumber(v) end end
  g.subp = {}
  if f[14] and f[14] ~= "-" then
    for _, e in ipairs(split(f[14], ",")) do
      local l, ph = string.match(e, "^(%d+)=(%d+)$")
      g.subp[tonumber(l)] = tonumber(ph)
    end
  end
  if f[13] ~= "-" then
    for _, c in ipairs(split(f[13], ",")) do
      local cue, still, cmd, secs = string.match(c, "^([^:]+):(%d+):(%d+):([%d%.]+)$")
      g.cells[#g.cells + 1] = { cue = cue, still = tonumber(still), cmd = tonumber(cmd), seconds = tonumber(secs) }
    end
  end
  for _, pt in ipairs((p.patches or {})[key] or {}) do
    if pt.idx == 0 then g[pt.sec] = hexcmds(pt.hex) else g[pt.sec][pt.idx] = pt.hex end
  end
  p.pgc[key] = g
  return g
end

local function getButtons(p, cue)
  local b = p.buttons[cue]
  if b ~= nil then return b or nil end
  local line = p.rawButtons[cue]
  if not line then p.buttons[cue] = false; return nil end
  local f = split(line, " ")
  b = { fsel = tonumber(f[3]), fact = tonumber(f[4]), hlStart = tonumber(f[5]), hlEnd = tonumber(f[6]),
        list = {}, byN = {} }
  for _, s in ipairs(split(f[7], ";")) do
    local v = split(s, ",")
    local btn = { n = tonumber(v[1]), x0 = tonumber(v[2]), y0 = tonumber(v[3]), x1 = tonumber(v[4]),
                  y1 = tonumber(v[5]), up = tonumber(v[6]), down = tonumber(v[7]), left = tonumber(v[8]),
                  right = tonumber(v[9]), auto = v[10] == "1", cmd = v[11] ~= "-" and v[11] or nil }
    b.list[#b.list + 1] = btn
    b.byN[btn.n] = btn
  end
  p.buttons[cue] = b
  return b
end

----------------------------------------------------------------- command decoding
-- A command is 16 hex digits; bits are numbered 63 (MSB) .. 0 as in libdvdnav.
local function cmdWords(hex)
  return tonumber(string.sub(hex, 1, 8), 16), tonumber(string.sub(hex, 9, 16), 16)
end

local function getbits(hi, lo, start, count)
  local low = start - count + 1
  if low >= 32 then return extract(hi, low - 32, count) end
  if start < 32 then return extract(lo, low, count) end
  local loBits = 32 - low                         -- field straddles the words
  -- math.floor keeps the result an integer on Lua 5.3+ hosts (Fengari), where 2 ^ n is a float
  return math.floor(extract(hi, 0, count - loBits) * 2 ^ loBits + extract(lo, low, loBits))
end

----------------------------------------------------------------- VM
function DVDVM.new(text, host)
  local vm = setmetatable({}, DVDVM)
  vm.prog = loadProgram(text)
  vm.host = host
  vm.gprm, vm.sprm = {}, {}
  for i = 0, 15 do vm.gprm[i] = 0 end
  for i = 0, 23 do vm.sprm[i] = 0 end
  vm.sprm[1], vm.sprm[2], vm.sprm[3] = 0, 62, 1       -- audio 0, subpicture off (62), angle 1
  vm.sprm[8] = 1024                                    -- HL_BTN 1
  vm.sprm[20] = 1                                      -- region
  vm.dom, vm.vts, vm.ttn = "FP", 0, 0
  vm.token = 0
  vm.resume = nil
  return vm
end

function DVDVM:log(...)
  if self.host.log then self.host.log(string.format(...)) end
end

function DVDVM:reg(r)
  if band(r, 0x80) ~= 0 then return self.sprm[band(r, 0x1f)] or 0 end
  return self.gprm[band(r, 0x0f)]
end

local function compare(op, a, b)
  if op == 1 then return band(a, b) ~= 0
  elseif op == 2 then return a == b
  elseif op == 3 then return a ~= b
  elseif op == 4 then return a >= b
  elseif op == 5 then return a > b
  elseif op == 6 then return a <= b
  elseif op == 7 then return a < b end
  return true
end

function DVDVM:rnd(n)
  if n == 0 then return 0 end
  if self.host.random then return self.host.random(n) end
  return math.random(1, n)
end

function DVDVM:setop(op, target, value, srcReg)
  local g, old = self.gprm, self.gprm[target]
  local v
  if op == 1 then v = value
  elseif op == 2 then                                    -- swap with a register source
    v = value
    if srcReg and band(srcReg, 0x80) == 0 then g[band(srcReg, 0x0f)] = old end
  elseif op == 3 then v = math.min(old + value, 0xFFFF)
  elseif op == 4 then v = math.max(old - value, 0)
  elseif op == 5 then v = math.min(old * value, 0xFFFF)
  elseif op == 6 then v = value == 0 and 0xFFFF or math.floor(old / value)
  elseif op == 7 then v = value == 0 and 0xFFFF or old % value
  elseif op == 8 then v = self:rnd(value)
  elseif op == 9 then v = band(old, value)
  elseif op == 10 then v = bor(old, value)
  elseif op == 11 then v = bxor(old, value)
  else return end
  g[target] = v
end

-- Link instruction of a group 1 (non-jump) / 2 / 3 command. Returns an action table or nil.
local function linkAction(hi, lo)
  local t = getbits(hi, lo, 51, 4)
  local btn = getbits(hi, lo, 15, 6)
  if t == 1 then
    local op = getbits(hi, lo, 4, 5)
    if op == 0 then return btn > 0 and { kind = "hl", button = btn } or nil end
    return { kind = "sub", op = op, button = btn }
  elseif t == 4 then return { kind = "pgcn", n = getbits(hi, lo, 14, 15) }
  elseif t == 5 then return { kind = "pttn", n = getbits(hi, lo, 9, 10), button = btn }
  elseif t == 6 then return { kind = "pgn", n = getbits(hi, lo, 6, 7), button = btn }
  elseif t == 7 then return { kind = "cn", n = getbits(hi, lo, 7, 8), button = btn }
  end
  return nil
end

-- Execute one command. Returns ("next") | ("goto", line) | ("break") | ("link", action)
function DVDVM:exec(hex)
  local hi, lo = cmdWords(hex)
  local grp = getbits(hi, lo, 63, 3)
  local op = getbits(hi, lo, 54, 3)
  local imm = getbits(hi, lo, 55, 1) == 1

  if grp == 0 then
    local cond = op == 0 or compare(op, self:reg(getbits(hi, lo, 39, 8)),
      imm and getbits(hi, lo, 31, 16) or self:reg(getbits(hi, lo, 23, 8)))
    if not cond then return "next" end
    local t = getbits(hi, lo, 51, 4)
    if t == 1 or t == 3 then return "goto", getbits(hi, lo, 7, 8) end
    if t == 2 then return "break" end
    return "next"

  elseif grp == 1 then
    if getbits(hi, lo, 60, 1) == 0 then                  -- link
      local cond = op == 0 or compare(op, self:reg(getbits(hi, lo, 39, 8)),
        imm and getbits(hi, lo, 31, 16) or self:reg(getbits(hi, lo, 23, 8)))
      if not cond then return "next" end
      local a = linkAction(hi, lo)
      if a then return "link", a end
      return "next"
    end
    local cond = op == 0 or compare(op, self:reg(getbits(hi, lo, 15, 8)), self:reg(getbits(hi, lo, 7, 8)))
    if not cond then return "next" end
    local t = getbits(hi, lo, 51, 4)
    if t == 1 then return "link", { kind = "exit" } end
    if t == 2 then return "link", { kind = "jumptt", tt = getbits(hi, lo, 22, 7) } end
    if t == 3 then return "link", { kind = "jumpvtstt", ttn = getbits(hi, lo, 22, 7) } end
    if t == 5 then return "link", { kind = "jumpvtsptt", ttn = getbits(hi, lo, 22, 7), ptt = getbits(hi, lo, 41, 10) } end
    if t == 6 or t == 8 then
      local a = { kind = t == 6 and "jumpss" or "callss", target = getbits(hi, lo, 23, 2),
                  menu = getbits(hi, lo, 19, 4), rsmCell = getbits(hi, lo, 31, 8) }
      -- vts/ttn exist only in JumpSS VTSM (from the VMG). CallSS VTSM always calls the
      -- current title set's menu; those bits belong to its rsm_cell field, which PgcEdit
      -- also misreads as "vts 1". Reading them sent Amazing Race's endings and credits
      -- (and 24's credits) to VTS 1's menus instead of their own.
      if a.target == 2 and t == 6 then a.vts, a.ttn = getbits(hi, lo, 30, 7), getbits(hi, lo, 38, 7) end
      if a.target == 3 then a.pgcn = getbits(hi, lo, 46, 15) end
      return "link", a
    end
    return "next"

  elseif grp == 2 then
    local cond = op == 0 or compare(op, self:reg(getbits(hi, lo, 15, 8)), self:reg(getbits(hi, lo, 7, 8)))
    if not cond then return "next" end
    local t = getbits(hi, lo, 59, 4)
    local i = getbits(hi, lo, 60, 1) == 1
    if t == 1 then                                       -- SetSTN
      local function stn(flagBit, slot)
        if getbits(hi, lo, flagBit, 1) == 1 then
          local v = getbits(hi, lo, flagBit - 1, 7)
          if not i then v = self:reg(v) end
          self.sprm[slot] = v
          if slot == 2 then self:reportSubpicture() end
        end
      end
      stn(39, 1); stn(31, 2); stn(23, 3)
    elseif t == 6 then                                   -- SetHL_BTN
      local v = i and getbits(hi, lo, 31, 16) or self:reg(getbits(hi, lo, 23, 8))
      self:setHighlight(rshift(v, 10))
    elseif t == 3 then                                   -- SetGPRMMD (counter mode not emulated)
      local v = i and getbits(hi, lo, 47, 16) or self:reg(getbits(hi, lo, 39, 8))
      self.gprm[getbits(hi, lo, 19, 4)] = v
    end
    local a = getbits(hi, lo, 51, 4) ~= 0 and linkAction(hi, lo)
    if a then return "link", a end
    return "next"

  elseif grp == 3 then
    local cond = op == 0 or compare(op, self:reg(getbits(hi, lo, 47, 8)),
      imm and getbits(hi, lo, 15, 16) or self:reg(getbits(hi, lo, 7, 8)))
    if not cond then return "next" end
    local i = getbits(hi, lo, 60, 1) == 1
    local srcReg = (not i) and getbits(hi, lo, 23, 8) or nil
    local value = i and getbits(hi, lo, 31, 16) or self:reg(srcReg)
    self:setop(getbits(hi, lo, 59, 4), getbits(hi, lo, 35, 4), value, srcReg)
    local a = getbits(hi, lo, 51, 4) ~= 0 and linkAction(hi, lo)
    if a then return "link", a end
    return "next"
  end
  self:log("unsupported command group %d: %s", grp, hex)
  return "next"
end

-- Run a command list from line 1. Returns a link action, or nil if it fell through.
function DVDVM:run(list, first)
  local pc, steps = first or 1, 0
  while pc >= 1 and pc <= #list do
    steps = steps + 1
    if steps > 10000 then self:log("command loop aborted"); return nil end
    local r, x = self:exec(list[pc])
    if r == "goto" then pc = x
    elseif r == "break" then return nil
    elseif r == "link" then return x
    else pc = pc + 1 end
  end
  return nil
end

----------------------------------------------------------------- domains / PGCs
-- Active physical subpicture stream, libdvdnav vm_get_subp_active_stream rules:
-- menus always use logical stream 0; titles use SPRM2 (minus the display flag), and when
-- that logical stream isn't available, the first one that is. Returns stream (or nil), shown.
function DVDVM:activeSubpicture()
  local g = self.pgc
  if not g or not g.subp then return nil, false end
  local inTitle = self.dom == "VTS"
  local sp = self.sprm[2] or 0
  local logical = inTitle and band(sp, 0xBF) or 0
  local stream = g.subp[logical]
  if stream == nil then
    for l = 0, 31 do
      if g.subp[l] ~= nil then stream = g.subp[l]; break end
    end
  end
  local shown = (not inTitle) or band(sp, 0x40) ~= 0
  return stream, shown
end

function DVDVM:reportSubpicture()
  if not self.host.subpicture then return end
  local s, shown = self:activeSubpicture()
  self.host.subpicture(s, shown)
end

function DVDVM:domainPrefix()
  if self.dom == "VMGM" or self.dom == "FP" then return "M0" end
  if self.dom == "VTSM" then return "M" .. self.vts end
  return "V" .. self.vts
end

function DVDVM:setHighlight(n)
  if n < 1 then return end
  self.sprm[8] = n * 1024
  if self.cellButtons and self.host.highlight then self.host.highlight(n) end
end

function DVDVM:stopWaits()
  if self.waitHandle and self.host.cancel then self.host.cancel(self.waitHandle) end
  self.waitHandle = nil
  self.token = self.token + 1
  self.cellButtons = nil
  if self.host.buttons then self.host.buttons(nil) end
end

-- skipPre: resume into a PGC (RSM) without re-running its pre commands.
-- Like libdvdnav, a PTT jump to program > 1 still runs the pre commands first.
function DVDVM:playPGC(key, startCell, skipPre)
  self:stopWaits()
  local g = getPGC(self.prog, key)
  if not g then self:log("missing PGC %s", key); return end
  self.pgc, self.cell = g, 0
  self:log("PGC %s", key)
  if not skipPre then
    local a = self:run(g.pre)
    if a then return self:doLink(a) end
  end
  startCell = startCell or 1
  if #g.cells == 0 then return self:pgcEnded() end
  self:playCell(startCell)
end

function DVDVM:playCell(n)
  self:stopWaits()
  local g = self.pgc
  if n > #g.cells then return self:pgcEnded() end
  self.cell = n
  for pg = #g.progmap, 1, -1 do
    if g.progmap[pg] <= n then self.program = pg; break end
  end
  if self.dom == "VTS" then self.sprm[7] = self:pttForProgram() or self.sprm[7] end
  local c = g.cells[n]
  local b = getButtons(self.prog, c.cue)
  self.cellButtons = b
  local token = self.token
  self.host.play(c.cue, n, token)
  self:reportSubpicture()
  if b and self.host.buttons then
    local hl = rshift(self.sprm[8], 10)
    if b.fsel > 0 and b.byN[b.fsel] then hl = b.fsel end
    if not b.byN[hl] then hl = b.list[1].n end
    self.sprm[8] = hl * 1024
    self.host.buttons(b.list, hl)
  end
end

function DVDVM:pttForProgram()
  local list = self.prog.ptt[self.vts .. ":" .. self.ttn]
  if not list then return nil end
  local pgcn = tonumber(string.match(self.pgc.key, "%.(%d+)$"))
  local best
  for i, e in ipairs(list) do
    if e.pgcn == pgcn and e.pgn <= (self.program or 1) then best = i end
  end
  return best
end

function DVDVM:cueEnded(token)
  if token ~= nil and token ~= self.token then return end
  local c = self.pgc and self.pgc.cells[self.cell]
  if not c then return end
  if c.still > 0 then
    if c.still == 255 then return end                    -- infinite still: wait for a button
    local t = self.token
    self.waitHandle = self.host.wait(c.still, function()
      if t == self.token then self:cellFinished() end
    end)
    return
  end
  self:cellFinished()
end

function DVDVM:cellFinished()
  local c = self.pgc.cells[self.cell]
  if c.cmd > 0 and self.pgc.cellcmd[c.cmd] then
    local r, a = self:exec(self.pgc.cellcmd[c.cmd])
    if r == "link" then return self:doLink(a) end
  end
  self:playCell(self.cell + 1)
end

function DVDVM:pgcEnded()
  local g = self.pgc
  if g.still > 0 then
    if g.still == 255 then self.atTail = true; return end
    local t = self.token
    self.waitHandle = self.host.wait(g.still, function() if t == self.token then self:runPost() end end)
    return
  end
  self:runPost()
end

function DVDVM:runPost()
  local g = self.pgc
  local a = self:run(g.post)
  if a then return self:doLink(a) end
  if g.next > 0 then return self:linkPGCN(g.next) end
  self:log("PGC %s ended with nowhere to go", g.key)
end

function DVDVM:linkPGCN(n, cell)
  if not n or n == 0 then self:log("link to PGC 0 ignored"); return end   -- e.g. GoUp with no GoUp PGC
  return self:playPGC(self:domainPrefix() .. "." .. n, cell)
end

-- Menu entry PGC of a domain ("M0" / "M<vts>") for a menu type (2..7)
function DVDVM:menuPGC(prefix, menuType)
  return self.prog.menuEntry[prefix .. ":" .. menuType]
end

function DVDVM:enterTitle(vts, ttn, ptt)
  local list = self.prog.ptt[vts .. ":" .. ttn]
  if not list then self:log("no title VTS%d TTN%d", vts, ttn); return end
  local e = list[ptt or 1]
  self.dom, self.vts, self.ttn = "VTS", vts, ttn
  self.sprm[5], self.sprm[7] = ttn, ptt or 1
  for tt, t in pairs(self.prog.titles) do
    if t.vts == vts and t.ttn == ttn then self.sprm[4] = tt end
  end
  self.sprm[6] = e.pgcn
  local g = getPGC(self.prog, "V" .. vts .. "." .. e.pgcn)
  local cell = 1
  if e.pgn > 1 and g then cell = g.progmap[e.pgn] or 1 end
  return self:playPGC("V" .. vts .. "." .. e.pgcn, cell)
end

function DVDVM:doLink(a)
  local k = a.kind
  if a.button and a.button > 0 then self:setHighlight(a.button) end
  if k == "hl" then return end
  if k == "pgcn" then return self:linkPGCN(a.n) end
  if k == "pgn" then
    local cell = self.pgc.progmap[a.n]
    if not cell then return self:pgcEnded() end
    return self:playCell(cell)
  end
  if k == "cn" then return self:playCell(a.n) end
  if k == "pttn" then return self:enterTitle(self.vts, self.ttn, a.n) end
  if k == "sub" then
    local op, g = a.op, self.pgc
    if op == 1 then return self:playCell(self.cell)                       -- LinkTopC
    elseif op == 2 then return self:playCell(self.cell + 1)               -- LinkNextC
    elseif op == 3 then return self:playCell(math.max(1, self.cell - 1))  -- LinkPrevC
    elseif op == 5 then return self:playCell(g.progmap[self.program or 1] or 1)            -- LinkTopPG
    elseif op == 6 then                                                    -- LinkNextPG
      local c = g.progmap[(self.program or 1) + 1]
      if c then return self:playCell(c) end
      return self:pgcEnded()
    elseif op == 7 then return self:playCell(g.progmap[math.max(1, (self.program or 1) - 1)] or 1)
    elseif op == 9 then return self:playPGC(g.key)                        -- LinkTopPGC
    elseif op == 10 then return self:linkPGCN(g.next)
    elseif op == 11 then return self:linkPGCN(g.prev)
    elseif op == 12 then return self:linkPGCN(g.goup)
    elseif op == 13 then self:stopWaits(); return self:runPost()          -- LinkTailPGC
    elseif op == 16 then return self:resumeTitle()                        -- RSM
    end
    return
  end
  if k == "exit" then self:stopWaits(); self:log("Exit"); return end
  if k == "jumptt" then
    local t = self.prog.titles[a.tt]
    self.resume = nil
    return self:enterTitle(t.vts, t.ttn)
  end
  if k == "jumpvtstt" then return self:enterTitle(self.vts, a.ttn) end
  if k == "jumpvtsptt" then return self:enterTitle(self.vts, a.ttn, a.ptt) end
  if k == "jumpss" or k == "callss" then
    if k == "callss" and self.dom == "VTS" then
      self.resume = { vts = self.vts, ttn = self.ttn, key = self.pgc.key,
                      cell = a.rsmCell > 0 and a.rsmCell or self.cell, sprm = { self.sprm[4], self.sprm[5], self.sprm[6], self.sprm[7] } }
    end
    if a.target == 0 then
      self.dom = "FP"; return self:playPGC("FP")
    elseif a.target == 1 then
      self.dom = "VMGM"
      local key = self:menuPGC("M0", a.menu)
      if not key then self:log("no VMGM %s menu", MENU_NAMES[a.menu] or a.menu); return end
      return self:playPGC(key)
    elseif a.target == 2 then
      local vts = (self.dom == "VMGM" or self.dom == "FP") and a.vts or self.vts
      if a.vts and a.vts > 0 then vts = a.vts end
      self.dom, self.vts = "VTSM", vts
      if a.ttn and a.ttn > 0 then self.ttn = a.ttn end
      local key = self:menuPGC("M" .. vts, a.menu)
      if not key then self:log("no VTSM%d %s menu", vts, MENU_NAMES[a.menu] or a.menu); return end
      return self:playPGC(key)
    else
      self.dom = "VMGM"
      return self:playPGC("M0." .. a.pgcn)
    end
  end
  self:log("unhandled link %s", k)
end

function DVDVM:resumeTitle()
  local r = self.resume
  if not r then self:log("RSM with no resume point"); return end
  self.resume = nil
  self.dom, self.vts, self.ttn = "VTS", r.vts, r.ttn
  self.sprm[4], self.sprm[5], self.sprm[6], self.sprm[7] = r.sprm[1], r.sprm[2], r.sprm[3], r.sprm[4]
  self:playPGC(r.key, r.cell, true)
end

----------------------------------------------------------------- input
function DVDVM:activate(n)
  local b = self.cellButtons
  local btn = b and b.byN[n]
  if not btn or not btn.cmd then return end
  self.sprm[8] = n * 1024
  if self.host.activated then self.host.activated(n) end
  local r, a = self:exec(btn.cmd)
  if r == "link" then return self:doLink(a) end
end

function DVDVM:press(key)
  local b = self.cellButtons
  if not b then return end
  local cur = b.byN[rshift(self.sprm[8], 10)] or b.list[1]
  if key == "ok" then return self:activate(cur.n) end
  local target = cur[key]
  if not target or target == 0 or target == cur.n then return end
  self:setHighlight(target)
  local t = b.byN[target]
  if t and t.auto then return self:activate(target) end
end

function DVDVM:click(n)
  local b = self.cellButtons
  if not b or not b.byN[n] then return end
  self:setHighlight(n)
  return self:activate(n)
end

function DVDVM:start()
  self.dom = "FP"
  self:playPGC("FP")
end

function DVDVM:state()
  return string.format("%s %s cell %d  g=[%s]", self.dom, self.pgc and self.pgc.key or "-", self.cell or 0,
    table.concat(self.gprm, ",", 0, 15))
end

-- Predict the cue that plays next if the current cell ends with no input, so the host
-- can load it ahead of time (seamless multi-cell PGCs). Runs the end-of-cell path on a
-- throwaway copy of the VM: cell command, next cell / post commands / links and the next
-- PGC's pre commands, up to the first cell that would play. rnd() is pinned to 1 there,
-- so a random branch can mispredict; the host then just loads on demand.
-- Returns cue, cell number, PGC key -- or nil (infinite still, PGC still, dead end).
function DVDVM:predictNext()
  local c = self.pgc and self.pgc.cells[self.cell]
  if not c or c.still == 255 then return nil end
  local sim = setmetatable({}, DVDVM)
  for k, v in pairs(self) do sim[k] = v end
  sim.gprm, sim.sprm = {}, {}
  for i = 0, 15 do sim.gprm[i] = self.gprm[i] end
  for i = 0, 23 do sim.sprm[i] = self.sprm[i] end
  if self.resume then
    local r = self.resume
    sim.resume = { vts = r.vts, ttn = r.ttn, key = r.key, cell = r.cell,
                   sprm = { r.sprm[1], r.sprm[2], r.sprm[3], r.sprm[4] } }
  end
  local cue, cell, key
  sim.waitHandle = nil
  sim.host = {
    play = function(q, n) if not cue then cue, cell, key = q, n, sim.pgc.key end end,
    buttons = function() end, highlight = function() end, subpicture = function() end,
    wait = function() return nil end, cancel = function() end,
    random = function() return 1 end,
  }
  local ok = pcall(function() sim:cellFinished() end)
  if not ok then return nil end
  return cue, cell, key
end

return DVDVM
