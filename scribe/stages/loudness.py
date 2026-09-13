"""Quieten a window's look-ahead so it cannot raise that window's log-mel floor.

faster-whisper extracts features once over the whole input it is handed
(`transcribe.py`) and then floors every log-mel bin at `log_spec.max() - 8`
(`feature_extractor.py`). The floor is therefore a property of the loudest bin
in the *input*, not of the audio a frame belongs to.

Since TASK-030 each window is decoded with 30 s of the next one appended, so a
look-ahead holding a louder bin than its window raises the floor for the whole
window and the decoder reads different features from frame 0 - a whole window
re-decoded because of audio that will be thrown away. Measured over 43
recordings (156 look-ahead windows): the floor rose in 7, and away from the cut
those windows lost real words - "Intellivision" became "television" three
times, "Novell NetWare" became "Novell network" five times, "CypherCon" became
"SeekerCon", and one recording dropped a sentence.

The fix is one multiplication. Scaling audio by `a` scales its power by `a²`,
so every log-mel bin of the look-ahead moves by `2·log10(a)`: ask for the
factor that puts the look-ahead's loudest bin at the window's own loudest bin,
and the combined input's maximum - and so its floor - is the window's own.
Measured after the change: the window's features are bit-identical to its
features without any look-ahead, except the two frames whose STFT support
straddles the junction, which hold look-ahead samples whatever their loudness.
The decoder still hears past the cut, only quieter, and in the seven windows it
touches it kept the end-of-window hallucinations away exactly as before.

Two asymmetries make the cheap version safe:

* The window's maximum is taken over the audio faster-whisper will really keep,
  its own VAD's speech chunks, because that is what it extracts features from.
  Taking it over everything could name a loud bin the VAD drops, aim too high
  and leave the floor raised - the bug this module exists to remove.
* The look-ahead's maximum is taken over all of it, VAD or no VAD. That is an
  upper bound for whatever the VAD keeps, so the factor is never too gentle.
  It also saves a second VAD pass on audio that is 30 s long.
"""

from __future__ import annotations

import numpy as np

MARGIN = 1e-5
"""Subtracted from the exponent, so the scaled maximum lands just under the
window's rather than on it: the log-mel maximum of the scaled audio is computed
in float32, and a tie decided by rounding would leave the floor raised."""


def log_mel_max(extractor, audio: np.ndarray) -> float:
    """The loudest log-mel bin of `audio`, before the floor.

    `extractor` is the model's own `FeatureExtractor` - the default model has
    128 mel bins and the one ADR-004 substitutes for translate has 80, and a
    maximum over the wrong filterbank is the wrong number. Its output is
    already floored and scaled as `(max(log_spec, max - 8) + 4) / 4`, and the
    maximum of that survives both steps, so inverting it costs nothing.
    """
    return 4.0 * float(extractor(audio).max()) - 4.0


def _speech(audio: np.ndarray) -> np.ndarray:
    """The audio faster-whisper's VAD would keep, concatenated as it does it.

    A window with no speech at all keeps everything: it has no maximum of its
    own to protect, and an empty array has no maximum to take.

    faster-whisper is imported here rather than at the top, because importing
    it is what loads CTranslate2, and ADR-006 has `cuda_setup.ensure_cuda_libs`
    run before that happens. The web process, which imports this package to
    reach the stages' names, must not pull a model runtime in at all.
    """
    from faster_whisper.vad import VadOptions, collect_chunks, get_speech_timestamps

    chunks = get_speech_timestamps(audio, VadOptions())
    if not chunks:
        return audio
    kept, _metadata = collect_chunks(audio, chunks)
    return np.concatenate(kept, axis=0) if kept else audio


def scale_lookahead(extractor, window: np.ndarray, lookahead: np.ndarray) -> np.ndarray:
    """The look-ahead to hand the decoder after `window`.

    The same array when it cannot raise the window's floor - which is the usual
    case, 149 of 156 windows measured - and otherwise a float32 copy, quiet
    enough that the combined input's loudest bin is the window's own.
    """
    if len(lookahead) == 0 or len(window) == 0:
        return lookahead
    ahead = log_mel_max(extractor, lookahead)
    here = log_mel_max(extractor, _speech(window))
    if ahead <= here:
        return lookahead
    return lookahead * np.float32(10.0 ** ((here - ahead) / 2.0 - MARGIN))
