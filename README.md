# MMDub

MMDub is a private local Windows studio for Burmese text-to-speech, timestamped subtitle narration, and source-video-to-Burmese dubbing. The server binds only to `127.0.0.1`; video, source audio, previews, edit plans, and exports remain on this computer.

## Start

Double-click `start.cmd`. First-time setup creates a Python 3.12 environment, installs the online speech client, local Whisper transcription, and bundled FFmpeg. The page opens at <http://127.0.0.1:7860>.

## Quick Voice

1. Enter up to 50,000 Burmese characters.
2. Choose Nilar, Thiha, Ava Multilingual, Andrew Multilingual, Brian Multilingual, Emma Multilingual, or Florian Multilingual.
3. Use automatic emotion analysis or choose an expressive delivery style manually.
4. Generate, play, and download the 24 kHz WAV.

Long input is split below the provider request limit. Each completed chunk is cached by text, voice, speed, style, and provider version, so retrying can reuse finished work. Speech needs internet and never silently falls back to the former local model.

## Video or RedNote → Burmese

1. Upload an MP4, MOV, MKV, or WebM, or paste a permitted RedNote/Xiaohongshu share link.
2. RedNote subtitles are reused when available. Otherwise a warm, persistent `faster-whisper` Small model detects and transcribes the source language locally.
3. Download the source SRT and import a translated Burmese SRT, or choose optional Gemini automatic translation. Choose one online voice for the whole video.
4. In the scene editor, change translation, expressive delivery, timing, picture ranges, scene order, or add caption-only sentences.
5. Style captions globally or per sentence with the bundled Noto Sans Myanmar, Padauk, Pyidaungsu, TharLon, and Myanmar Sans Pro fonts. Adjust size, color, and safe-area or custom position directly over the video preview.
6. Export voice-over with captions, voice-over without burned captions, or a subtitle-only video that preserves the original audio. The standalone Burmese SRT is always available.

Long exports are split into checkpointed chunks near scene boundaries. RTX NVENC is preferred, with an automatic CPU encoder fallback if hardware encoding cannot start. Pause, restart, and retry reuse completed chunks when their source and edit settings have not changed, while the previous successful MP4 stays playable until a replacement passes validation.

Video frames always remain at native 1× speed. Narration is accelerated without pitch shifting when it must fit a source cue, with a warning above 1.5×. The selected voice remains unchanged while restrained rate, pitch, volume, and pauses approximate emotion.

### Add a Gemini API key

1. In MMDub, open **Translate video** and click **Translation keys**.
2. Paste one or more Gemini API keys in the **Add or replace keys** text box, with one key per line.
3. Click **Encrypt & save**, then **Test saved keys**. MMDub stores keys encrypted for your Windows account and never shows a saved key again.

Gemini keys are entered through the app's key editor. Do not put API keys in source files, the README, screenshots, or GitHub. The repository ignores local environment files, credentials, and secret folders. Use Manual SRT to translate without sending transcript text to Gemini.

## Subtitle to Speech (STS)

1. Open **Subtitle voice** and paste or import a UTF-8 `.srt` file containing Burmese subtitles.
2. Choose any online voice, speed, and automatic or manual expressive delivery.
3. Generate timestamp-aligned narration and review any cues that needed speed adjustment to fit their window.
4. Play or download the synchronized WAV and original SRT.

STS supports up to 50 minutes and 50,000 subtitle characters. Silence and cue start times are preserved in the exported 24 kHz WAV.

## Privacy

- The source video and audio never leave this computer.
- Manual SRT mode sends no transcript text to a translation service. Transcript text is sent to Gemini only when automatic translation is selected.
- Burmese text is sent to Microsoft Edge online speech.
- RedNote links are encrypted with Windows DPAPI; only the host and content identifier are exposed by the API. Browser cookies are read temporarily by the downloader and are never copied into app storage.
- Existing custom recordings and historical local generations are preserved under `data/`, but archived recordings are not offered for new speech.
- Gemini keys are added through the **Translation keys** editor, encrypted with Windows DPAPI, and kept in the ignored local `data/secrets/` folder; they are not stored in source code, SQLite, or Git.
- Online speech is unofficial, requires internet, and may be throttled or changed by its provider.

## Development

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
py -3.12 -m pytest -q
.\.venv\Scripts\python.exe .\run.py
```
