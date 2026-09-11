"""The transcribe options: one model for every door a recording comes through.

`TranscribeOptions` is what the dialog asks and what `POST /api/media` takes
as fields, and its `to_params()` is the job's `params_json`. It lives here,
outside `scribe.web`, so the JSON spine (`scribe.app`) can share it with the
HTML dialog without importing a router: routers depend on this module, and
nothing here depends on a router, a template or a request. `parse_options`
does speak HTTP - a 400 that names the field - because both callers are
routes and the error text should be shaped in one place.

Model names are never spelled here: a tier maps through
`transcribe.TIER_MODELS` (ADR-004), the language codes are
`transcribe.LANGUAGE_CODES`, and nothing is loaded - the stage module holds
tables, not weights (ADR-001).
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError, field_validator, model_validator

from scribe.stages import transcribe

# Speaker-count hints beyond this are a typo, not a meeting.
MAX_SPEAKERS = 50


class TranscribeOptions(BaseModel):
    """What the dialog asks, and what `POST /api/media` takes as fields.

    Built to be fed straight from a form: blank strings mean "not set", a
    checkbox's "0"/"1" is a bool, and a number arrives as text. `language`
    None is auto-detect. Anything the pipeline would choke on - a language
    Whisper does not know, a tier that is not one of the two, a speaker count
    of zero - is refused here, with the field named.
    """

    language: str | None = None
    tier: Literal["turbo", "max"] = "turbo"
    diarize: bool = True
    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None
    translate: bool = False

    @field_validator("language", "num_speakers", "min_speakers", "max_speakers", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("language")
    @classmethod
    def _known_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = value.strip().lower()
        if code not in transcribe.LANGUAGE_CODES:
            raise ValueError(f"unknown language code {value!r}; leave it empty to auto-detect")
        return code

    @field_validator("num_speakers", "min_speakers", "max_speakers")
    @classmethod
    def _a_sensible_count(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= MAX_SPEAKERS:
            raise ValueError(f"speaker counts run from 1 to {MAX_SPEAKERS}")
        return value

    @model_validator(mode="after")
    def _min_at_most_max(self) -> "TranscribeOptions":
        if (
            self.min_speakers is not None
            and self.max_speakers is not None
            and self.min_speakers > self.max_speakers
        ):
            raise ValueError("min_speakers cannot exceed max_speakers")
        return self

    def to_params(self) -> dict:
        """The job params the runner's stages read.

        The tier becomes a model name; translate becomes the task. Translate on
        turbo is left as turbo on purpose: `transcribe.resolve_model` makes the
        large-v3 substitution at run time and writes the note into the run row,
        which is how the transcript view can say it happened.
        """
        params: dict = {
            "model": transcribe.TIER_MODELS[self.tier],
            "task": "translate" if self.translate else "transcribe",
            "language": self.language,
            "diarize": self.diarize,
        }
        for key in ("num_speakers", "min_speakers", "max_speakers"):
            value = getattr(self, key)
            if value is not None:
                params[key] = value
        return params


OPTION_FIELDS: tuple[str, ...] = tuple(TranscribeOptions.model_fields)

PARAM_KEYS: frozenset[str] = frozenset(
    {"model", "task", "language", "diarize", "num_speakers", "min_speakers", "max_speakers"}
    | {transcribe.EXTRA_HOTWORDS_KEY}
    | {"device", "compute_type"}
)
"""Every key a transcribe job may carry, whoever produced it.

Kept beside `to_params` because the two drift otherwise, and pinned by a test
that calls `to_params` and checks nothing escaped. It exists so that params
from another job type cannot be carried into a transcribe request wholesale -
which is how a chat model reached the transcribe stage on 2026-09-03
(`scribe/web/library.py:_last_params`).

The dialog is not the only producer, and saying "every key to_params can
produce" made this sieve wrong the day it was written: a URL import puts the
video's own proper nouns in `extra_hotwords` (`url_stage.transcribe_params`)
and the transcribe stage reads them (`transcribe._extra_hotwords`). Left out,
re-transcribing an imported video silently dropped the names the ingest door
already knew. Any new key a transcribe job may carry belongs here too.

Since TASK-034 it is also the door: `POST /api/media` refuses a params object
carrying anything else, and a retry replays only these. That door used to take
any key, and the diarize stage read one of them - `diarization_model` - into
pyannote's `Pipeline.from_pretrained`, which resolves the class a pipeline
config names and loads its checkpoints with `weights_only=False`. Whoever could
post to loopback chose code the runner ran. The stage no longer reads that key
and it is deliberately absent here.

`device` and `compute_type` were the drift the paragraph above warns about,
found the same day: both stages read `device` (`transcribe.run`,
`diarize.run`) and the transcribe stage reads `compute_type`, and the
end-to-end suite posts them to `/api/media` to run `tiny` on the CPU of a
machine with a card. Neither reaches a class-name resolver or an unpickler -
CTranslate2 and torch refuse a device string they do not know - so admitting
them reopens nothing. The price: a re-transcribe now repeats a request's
device, where `_last_params` used to drop it.
"""


def replayable(params: dict) -> dict:
    """The part of a stored transcribe request that may be asked for again.

    Two sieves, the two `library._last_params` applies to a re-transcribe:
    only the keys a transcribe job takes (PARAM_KEYS), and `model` only when it
    names a speech model. The second exists because `model` is a key a chat job
    has too: on 2026-09-03 a transcribe job carried `openai/gpt-5.6-luna` and
    failed on a HuggingFace 404, and the stage now refuses such a name up front
    (`transcribe.ensure_speech_model`) - so replaying it verbatim fails the
    same way every time. Dropped, the job falls back to the default tier and
    the row heals.

    A copy with the kept keys in their stored order, so a clean request comes
    back byte for byte the same once `jobs.enqueue` has json-encoded it.
    """
    kept = {key: value for key, value in params.items() if key in PARAM_KEYS}
    if "model" in kept and not transcribe.is_speech_model(str(kept["model"])):
        kept.pop("model")
    return kept


def parse_options(source: Mapping[str, Any]) -> TranscribeOptions:
    """Validated options out of a form or a JSON object; a 400 names the field.

    Only the option fields are looked at, so the same mapping may carry a
    title, a folder or an upload without any of them getting in the way.
    """
    given = {key: source[key] for key in OPTION_FIELDS if key in source}
    try:
        return TranscribeOptions.model_validate(given)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'options'}: {error['msg']}"
            for error in exc.errors()
        )
        raise HTTPException(status_code=400, detail=f"invalid transcribe options: {problems}")
