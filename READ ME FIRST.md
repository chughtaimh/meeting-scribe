# Meeting Scribe

Your private voice-to-text and meeting transcription app. It runs entirely on your Mac — the only thing that ever leaves your machine is the audio sent to OpenAI for transcription.

## Install (once, ~5 minutes)

1. **Drag `Meeting Scribe` onto the `Applications` folder** in this window.

2. **Open it from Applications.** The first time, macOS will block it with
   *"Apple could not verify 'Meeting Scribe' is free of malware."* That's
   because the app isn't registered with Apple — it's safe. To open it anyway:
   - Click **Done** (not "Move to Trash").
   - Open **System Settings → Privacy & Security**, scroll down to
     **Security** — you'll see *"Meeting Scribe" was blocked…*
   - Click **Open Anyway**, then confirm (macOS may ask for your password).

   This only happens once. (Prefer the Terminal? This one line does the same:
   `xattr -dr com.apple.quarantine "/Applications/Meeting Scribe.app"`)

3. **First launch — let it set itself up.**
   - If macOS pops up *"…requires the command line developer tools"*, click
     **Install** (one-time, ~5 minutes), then open Meeting Scribe again.
   - A Terminal window appears showing *"First-time setup — preparing Meeting
     Scribe"*. It downloads a few components (1–3 minutes, needs internet),
     then your browser opens the app automatically. You can close the
     Terminal window once it says so.

4. **Add your API key.** In the app, go to **Settings**, paste the key you
   were given (starts with `sk-`), and click **Save & test**.

5. **Allow the microphone** when your browser asks the first time you record.

After that, launching Meeting Scribe from Applications takes seconds and goes straight to the browser.

## The two buttons

**Transcribe** — quick voice-to-text. Speak, hit stop, and copy the text. Perfect for dictating notes, emails, or ideas.

**Meeting Recording** — records the room, then produces a transcript **broken up by speaker** ("Speaker A", "Speaker B", …), with timestamps, an AI-written title, an executive summary, decisions, and action items. Click any speaker name to replace it with the person's real name — the transcript and search index update everywhere.

> **In-person meetings and dictation work out of the box.** Capturing the *other side* of a video call (Zoom/Meet audio) needs a one-time optional setup — see "Optional: capture computer audio" below.

## Where transcripts live

Every recording is saved to **Documents → Meeting Scribe Transcripts**, one folder per recording:

- `audio.webm` — the original recording (playable inside the app, with click-to-jump timestamps)
- `transcript.md` — the readable transcript (opens in any text editor)
- `transcript.json` — structured data

Change the destination folder anytime in **Settings → Transcripts folder**.

## Search

The search bar (Home or Library) searches **all** transcripts two ways at once: exact keywords, and *by meaning* — so "pushback on agency pricing" finds the right meeting even if nobody used those exact words. Results show who said it, when, and jump you to that exact moment, with the audio cued up.

## What it costs

Transcription ≈ **$0.36 per hour of audio**; summaries and search cost fractions of a cent. Settings shows your running usage for the month. The key you were given has a monthly limit — if transcription suddenly stops working with a billing error, ask the person who gave you the key.

## Optional: capture computer audio (Zoom/Meet calls)

By default the app records your **microphone** only. To also record what comes out of your speakers (the other people on a call), do this once:

1. **Install BlackHole** (free, notarized audio driver): download
   [BlackHole 2ch](https://existential.audio/downloads/BlackHole2ch-0.7.1.pkg)
   and run the installer.
2. **Create the "Meeting Scribe Output" device**: open Terminal and paste:
   ```
   swiftc -O "/Applications/Meeting Scribe.app/Contents/Resources/MeetingScribe/tools/setup_audio.swift" -o /tmp/setup_audio && /tmp/setup_audio
   ```
   (Uses the same developer tools installed during setup.)
3. **Keep your Mac's sound output set to "Meeting Scribe Output"**
   (Control Center → Sound). Then leave **"Also capture computer audio"**
   ticked when recording.

Two caveats: the volume keys don't adjust a multi-output device (change volume on the underlying speakers/headphones in Sound settings), and if macOS switches output (AirPods connect, display unplugged) just re-select Meeting Scribe Output — the app warns you mid-recording if computer audio goes silent.

## Troubleshooting

- **"Apple could not verify…" and no Open Anyway button:** try opening the app again, then go straight to System Settings → Privacy & Security. The Open Anyway button appears for about an hour after a blocked attempt.
- **"Python 3 is needed":** macOS shows an install popup — click Install, wait, then launch again (one-time).
- **Mic doesn't record:** System Settings → Privacy & Security → Microphone → enable your browser. Then reload the app.
- **"Computer-audio capture isn't set up":** see "Optional: capture computer audio" above; check that **Meeting Scribe Output** is your Mac's sound output.
- **Key errors:** Settings → Save & test shows exactly what's wrong (invalid key, no credit, etc.).
- **Something else:** send the log file to whoever gave you the app. It lives at
  `~/Library/Application Support/MeetingScribe/data/scribe.log`.
- **Quitting:** the app runs quietly in the background; quit it from **Settings → Quit Meeting Scribe**.

## Privacy

Audio is sent to OpenAI's API for transcription and the text of your transcripts is sent for summaries/search-indexing, under OpenAI's API data terms (API data is not used to train their models). Everything else — recordings, transcripts, the search index, your API key — stays on your Mac.
