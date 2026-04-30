# Traktor setup on this MacBook

Aggregate device + Traktor routing for: laptop speakers as **master**, Z1
headphone jack as **monitor (cue)**. No external speakers, no Bluetooth.

## Hardware

- MacBook Pro (built-in speakers used for master)
- Native Instruments Traktor Kontrol Z1 (USB → main + headphone outs)
- Native Instruments Traktor Kontrol X1 (deck/effect/loop control, no audio)
- Wired headphones into the Z1's 3.5mm jack

## Aggregate device

Audio MIDI Setup → "+" → **Create Aggregate Device**

| Setting | Value |
|---|---|
| Name | `Traktor Setup` |
| Members (in this order) | 1. MacBook Pro Speakers  2. Traktor Kontrol Z1 |
| Clock Source | Traktor Kontrol Z1 |
| Drift Correction | **ON** for MacBook Pro Speakers, OFF for Z1 |
| Sample Rate | 44.1 kHz on both members |

Member order matters — it determines channel numbering in Traktor.

## Channel mapping (in the aggregate)

| Channel | Physical destination |
|---|---|
| 1–2 | MacBook Pro Speakers (stereo) |
| 3–4 | Z1 Main out (RCA — currently unused) |
| 5–6 | Z1 Headphone out |

## Traktor routing

Preferences → Audio Setup → **Audio Device:** `Traktor Setup`

Preferences → Output Routing → **Mixing Mode:** Internal

| Output | L | R |
|---|---|---|
| Master | ch 1 | ch 2 |
| Monitor (cue) | ch 5 | ch 6 |

## Why this works

- Both outputs are wired (no Bluetooth in the chain) → ~5–15 ms latency on
  both paths, beatmatching feels natural.
- Z1 is the clock master because its USB audio clock is purpose-built for
  low-jitter audio; the Mac's internal codec clock is less stable.
- Drift correction on the speakers compensates for the small clock-rate
  difference between the Z1 and the Mac's audio chip — prevents long-session
  drift.
- The aggregate persists across reboots and unplug/replug. Traktor's routing
  is also persistent.

## Tradeoffs

- **Laptop speakers have no real bass below ~80 Hz** — fine for practising
  transitions and building mix flow, useless for evaluating how the mix will
  sound on a real PA. For finished-mix evaluation use proper monitors or
  decent headphones.
- **Avoid pulling the Z1 while Traktor is open** — quit Traktor first to
  prevent stuck audio engine / crackles on the next launch.

## Backup

Traktor settings: `docs/traktor-backups/` — export from Preferences → Export.
The aggregate device itself is not exportable in any portable form; recreate
it from this recipe.
