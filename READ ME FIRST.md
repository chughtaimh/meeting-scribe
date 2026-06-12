# Meeting Scribe

Your private voice-to-text and meeting transcription app. It runs entirely on your Mac — the only thing that ever leaves your machine is the audio sent to OpenAI for transcription.

## Get started (3 steps, ~3 minutes)

1. **Double-click `Start Meeting Scribe.command`** in this folder.
   The first launch sets itself up (1–3 minutes, needs internet). Every launch after that takes seconds. Your browser opens the app automatically.

2. **Add your OpenAI API key.**
   In the app, go to **Settings**, paste your key (starts with `sk-`), and click **Save & test**. If you need a key: platform.openai.com/api-keys → "Create new secret key".

3. **Allow the microphone** when your browser asks the first time you record.

That's it. After the first run you can use either `Start Meeting Scribe.command` or the `Meeting Scribe.app` icon — both start the same app.

## The two buttons

**Transcribe** — quick voice-to-text. Speak, hit stop, and copy the text. Perfect for dictating notes, emails, or ideas.

**Meeting Recording** — records the room, then produces a transcript **broken up by speaker** ("Speaker A", "Speaker B", …), with timestamps, an AI-written title, an executive summary, decisions, and action items. Click any speaker name to replace it with the person's real name — the transcript and search index update everywhere.

Tip for video calls: play the other side through your **speakers** (not headphones) so the microphone hears everyone.

## Where transcripts live

Every recording is saved to the **Transcripts** folder here, one folder per recording:

- `audio.webm` — the original recording (playable inside the app, with click-to-jump timestamps)
- `transcript.md` — the readable transcript (opens in any text editor)
- `transcript.json` — structured data

Change the destination folder anytime in **Settings → Transcripts folder**. New recordings follow the new setting.

## Search

The search bar (Home or Library) searches **all** transcripts two ways at once: exact keywords, and *by meaning* (vector search) — so "pushback on agency pricing" finds the right meeting even if nobody used those exact words. Results show who said it, when, and jump you to that exact moment, with the audio cued up.

## What it costs

Transcription ≈ **$0.36 per hour of audio**; summaries and search cost fractions of a cent. A $20/month budget covers roughly **50 hours of meetings**. Settings shows your running usage for the month.

## Troubleshooting

- **The launcher opens in a text editor or "can't be opened":** open Terminal, type `cd ` (with a space), drag this folder onto the Terminal window, press Return, then paste:
  `chmod +x "Start Meeting Scribe.command" "MeetingScribe/launch.sh" "Meeting Scribe.app/Contents/MacOS/MeetingScribe"`
  and press Return. Double-click the launcher again.
- **macOS blocks the app the first time:** right-click → Open → Open.
- **"Python 3 is needed":** macOS shows an install popup — click Install, wait, then launch again (one-time).
- **Mic doesn't record:** System Settings → Privacy & Security → Microphone → enable your browser. Then reload the app.
- **Key errors:** Settings → Save & test shows exactly what's wrong (invalid key, no credit, etc.).
- **Something else:** the log lives at `MeetingScribe/data/scribe.log`.
- **Quitting:** the app runs quietly in the background; quit it from **Settings → Quit Meeting Scribe**.

## Privacy

Audio is sent to OpenAI's API for transcription and the text of your transcripts is sent for summaries/search-indexing, under OpenAI's API data terms (API data is not used to train their models). Everything else — recordings, transcripts, the search index, your API key — stays in this folder on your Mac.
