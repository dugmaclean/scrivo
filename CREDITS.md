# Credits and thanks

Scrivo stands on the shoulders of other people's good work. Thank you to all of them.

## The experience that inspired it

**[Wispr Flow](https://wisprflow.ai)** showed how pleasant dictation can be: a small floating
bubble on the phone, a quiet bar on the desktop, a hold-or-tap shortcut, live feedback while you
speak, and text that arrives already tidied, in whatever box you are typing in. Scrivo set out
to offer that kind of experience for people who want to run everything on their own computers
and choose their own models. If you want a polished, supported product, Wispr Flow is well worth
a look.

Scrivo is an independent project. It is not affiliated with, endorsed by or connected to
Wispr Flow or its makers. Wispr Flow is a trademark of its owner, used here only to say where
the inspiration came from.

## Projects we studied for ideas

Before writing Scrivo we read the source of three open projects to learn from their
approaches. No code or text from them is included in Scrivo; every line here was written
for this project.

- **[Orpheus](https://github.com/CocaKova/orpheus)** by CocaKova (MIT License): a self-hosted
  speech service with an Android floating bubble and desktop clients. It showed how cleanly a
  phone, a desktop and one home server can share the work.
- **[WhisperClientAndroid](https://github.com/nihal111/WhisperClientAndroid)** by nihal111: a
  lightweight Android bubble that talks to a self-hosted speech server. Its way of finding the
  focused text box and falling back to the clipboard helped shape how Scrivo delivers text.
- **[OpenWhispr for Android](https://github.com/EdiBianco/OpenWhispr)** by EdiBianco (Apache
  License 2.0): on-device and server dictation with optional AI cleanup. It was a useful example
  of offering cleanup as a choice rather than a requirement.

## Software Scrivo uses

| Software | What it does here | License |
|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) by SYSTRAN | Runs the speech recognition models | MIT |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) by OpenNMT | Fast inference engine under faster-whisper | MIT |
| [Whisper](https://github.com/openai/whisper) models by OpenAI | The speech recognition models themselves | MIT (code and model weights) |
| [Silero VAD](https://github.com/snakers4/silero-vad) | Finds the speech in a recording (used through faster-whisper) | MIT |
| [aiohttp](https://github.com/aio-libs/aiohttp) | The server's web framework | Apache 2.0 |
| [NumPy](https://numpy.org) | Audio sample handling | BSD 3-Clause |
| [Hugging Face Hub](https://github.com/huggingface/huggingface_hub) | Downloads the speech models on first use | Apache 2.0 |
| Android SDK and platform libraries | The phone app | Apache 2.0 |
| [Gradle](https://gradle.org) wrapper and the Android Gradle plugin | Build the phone app (`android/gradlew` and `android/gradle/wrapper` are Gradle's own files) | Apache 2.0 |
| Python standard library | The Windows client uses nothing else | PSF License |

Writing models are whatever you point Scrivo at: a local server such as
[Ollama](https://ollama.com) or [llama.cpp](https://github.com/ggml-org/llama.cpp), or a cloud
service you choose. Each model has its own license and terms; please check them for the models
you use.

## What Scrivo adds

- One small self-hosted server that phones and computers share: one dictionary of saved words
  and one set of model choices for every device.
- Words recognized while you are still speaking, so finishing usually takes a moment, not a
  wait.
- Three ordered backups for recognition and three for writing. If every writing model fails you
  still get the words exactly as heard, and the recording is kept so nothing has to be said
  twice.
- A guard that refuses a writing model's own chatter, or a reply that leaves out most of what
  you said, so only your words reach the text box.
- Any OpenAI-compatible model for the tidy-up, local by default, with a clear notice before any
  cloud choice sends words off your machine.
- An Android bubble and a Windows hold-or-tap shortcut with a tiny teal bar, both written from
  scratch.
