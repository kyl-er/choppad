# Android Phone USB-MIDI Setup for ChopPad (Pixel 11 Pro XL)

## 1. Confirmed Hardware & Linux Audio State (2026-10-25)
- **Phone:** Google Pixel 11 Pro XL connected via USB-C data cable.
- **ALSA Sequencer (`aconnect -l`):** Enumerates natively as:
  ```text
  client 28: 'Pixel 11 Pro XL' [type=kernel,card=3]
      0 'Pixel 11 Pro XL MIDI 1'
  ```
- **Linux Audio Configuration:**
  - User `neo` is in `realtime` and `audio` groups.
  - PipeWire graph quantum is set to `256` frames (`5.33 ms` at 48 kHz).
  - Measured latency: **1.0–2.5 ms** USB-MIDI → ALSA `/dev/snd/seq` → Chrome `MIDIMessageEvent`, plus **16–24 ms** Chrome Web Audio output latency (**10–13 ms** if Chrome is launched with `--alsa-output-device=default`).

## 2. Enabling USB-MIDI on Android
1. Connect the Pixel 11 Pro XL to the laptop with a data-capable USB-C cable.
2. Swipe down the notification shade and tap **Charging this device via USB**.
3. Under *Use USB for*, select **MIDI**.
   - *Tip:* Android resets USB mode to *No data transfer* on every unplug. To make MIDI the default when unlocked, enable **Developer options → Default USB configuration → MIDI**.

## 3. Recommended Android MIDI Drum-Pad Apps
ChopPad defaults to **GM/MPC pads on MIDI notes 36–51 (`C1`–`D#2`, any channel)** and includes **1-click 16-pad MIDI Learn** (`localStorage["choppad.midiMap"]`) plus **Full Level** mode (default ON, ignoring erratic touchscreen velocity):
1. ** TouchDAW / MIDI Keyboard & Pads** (or any class-compliant USB-MIDI pad controller app):
   - Select **Android USB Peripheral Port** as the MIDI Output.
   - Either set the 4×4 pad bank to notes `36–51` (`C1` bottom-left to `D#2` top-right), **or** click **MIDI Learn** in ChopPad and tap pads 1→16 in order from bottom-left to top-right.

## 4. Essential Android Touch & Power Tweaks for Finger-Drumming
1. **Prevent Screen Sleep While Drumming:**
   - Enable **Settings → System → Developer options → Stay awake** (*Screen will never sleep while charging/plugged into USB*). If the screen dims or locks, Android suspends USB-MIDI touch delivery.
2. **Disable 3-Finger Screenshot / System Gestures:**
   - Multi-finger pad hits (chords + drum rolls with 3 fingers) can trigger Android's 3-finger screenshot or split-screen gesture, swallowing `pointerdown` events. Disable 3-finger gestures under **Settings → System → Gestures** while playing.
3. **Browser Autoplay Unlock:**
   - Chrome on Linux does not treat incoming Web MIDI messages as a user activation gesture for `AudioContext`. Click anywhere on the ChopPad page (or press any key) once after loading to dismiss the **"Click to enable audio"** banner.
4. **Hot-Plug & ALSA Port IDs:**
   - Unplugging and re-plugging USB changes the ALSA client number, but ChopPad's `createInput()` listens to `midiAccess.onstatechange` and automatically attaches to hot-plugged `Pixel 11 Pro XL MIDI 1` ports without reloading the page.
