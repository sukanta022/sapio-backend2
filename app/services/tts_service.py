import io
import json
import re
import shutil
import subprocess
import tempfile
import uuid
import wave

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from piper import PiperVoice, SynthesisConfig

from app.core.config import get_settings
from app.services.object_storage import cloud_enabled, audio_key, upload_path, delete_object, ensure_local, ObjectStorageError


class TTSError(Exception):
    """Audio generation failed."""


class TTSLimitError(TTSError):
    """Script is too large for synchronous TTS."""


@dataclass(frozen=True)
class AudioResult:
    relative_path: str
    file_size: int
    duration_seconds: int
    chunks_processed: int


# These are Sapio narrator profiles, not Piper model names.
# English currently uses a single installed voice.
VOICE_MAP = {
    "en": {
        "James": ("en_US-lessac-medium", 0),
        "Elena": ("en_US-lessac-medium", 0),
        "Marcus": ("en_US-lessac-medium", 0),
        "Sophia": ("en_US-lessac-medium", 0),
    },
    "bn": {
        "James": ("bn_BD-google-medium", 0),
        "Elena": ("bn_BD-google-medium", 1),
        "Marcus": ("bn_BD-google-medium", 2),
        "Sophia": ("bn_BD-google-medium", 3),
    },
}


def normalize_narration(text: str) -> str:
    """Remove common Markdown narration artifacts."""
    text = re.sub(
        r"(?m)^\s{0,3}#{1,6}\s*",
        "",
        text,
    )
    text = re.sub(
        r"(?m)^\s*[-*_]{3,}\s*$",
        "",
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\([^)]+\)",
        r"\1",
        text,
    )
    text = text.replace("**", "")
    text = text.replace("__", "")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def split_tts_text(
    text: str,
    max_characters: int,
) -> list[str]:
    """Split text without losing words or characters."""
    if max_characters < 1:
        raise ValueError(
            "max_characters must be positive."
        )

    cleaned = normalize_narration(text)

    if not cleaned:
        raise TTSError(
            "Narration script is empty."
        )

    chunks = []
    current = ""

    for word in cleaned.split():
        candidate = (
            f"{current} {word}".strip()
            if current
            else word
        )

        if len(candidate) <= max_characters:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        # Handle a word longer than the chunk limit.
        while len(word) > max_characters:
            chunks.append(word[:max_characters])
            word = word[max_characters:]

        current = word

    if current:
        chunks.append(current)

    return chunks


def get_voice(
    language_code: str,
    narrator_name: str,
) -> tuple[str, int]:
    selection = VOICE_MAP.get(
        language_code, {}
    ).get(narrator_name)

    if selection is None:
        raise TTSError(
            "Unsupported narrator and language combination."
        )

    return selection


def get_voice_path(model_name: str) -> Path:
    models_dir = get_settings().PIPER_MODELS_DIR.resolve()
    path = models_dir / f"{model_name}.onnx"
    config = models_dir / f"{model_name}.onnx.json"

    if not path.is_file() or not config.is_file():
        raise TTSError(
            f"Piper voice model '{model_name}' "
            "is not installed."
        )

    return path


def verify_speaker(
    model_path: Path,
    speaker_id: int,
) -> None:
    config_path = Path(str(model_path) + ".json")

    try:
        metadata = json.loads(
            config_path.read_text(encoding="utf-8")
        )
        speaker_count = int(
            metadata.get("num_speakers", 1)
        )
    except (OSError, ValueError, TypeError):
        raise TTSError(
            "Piper voice configuration is invalid."
        ) from None

    if not 0 <= speaker_id < speaker_count:
        raise TTSError(
            "Selected narrator speaker is not "
            "available in this Piper model."
        )


@lru_cache(maxsize=4)
def load_piper_voice(model_path: str) -> PiperVoice:
    return PiperVoice.load(model_path)


def piper_synthesize(
    text: str,
    model_path: Path,
    speaker_id: int,
) -> bytes:
    """Return a complete WAV segment from local Piper."""
    try:
        voice = load_piper_voice(str(model_path))

        buffer = io.BytesIO()

        config = SynthesisConfig(
            speaker_id=speaker_id
        )

        with wave.open(buffer, "wb") as output:
            voice.synthesize_wav(
                text,
                output,
                syn_config=config,
            )

        result = buffer.getvalue()

        if not result:
            raise TTSError(
                "Piper returned empty audio."
            )

        return result

    except TTSError:
        raise
    except Exception:
        raise TTSError(
            "Local Piper speech synthesis failed."
        ) from None


def combine_wav_segments(
    segments: list[bytes],
    output_path: Path,
) -> float:
    if not segments:
        raise TTSError(
            "No WAV segments were generated."
        )

    expected_format = None
    total_frames = 0

    try:
        with wave.open(str(output_path), "wb") as output:
            for segment in segments:
                with wave.open(
                    io.BytesIO(segment),
                    "rb",
                ) as source:
                    current_format = (
                        source.getnchannels(),
                        source.getsampwidth(),
                        source.getframerate(),
                        source.getcomptype(),
                    )

                    if expected_format is None:
                        expected_format = current_format

                        if (
                            current_format[0] != 1
                            or current_format[1] != 2
                            or current_format[3] != "NONE"
                        ):
                            raise TTSError(
                                "Unsupported Piper WAV format."
                            )

                        output.setnchannels(
                            current_format[0]
                        )
                        output.setsampwidth(
                            current_format[1]
                        )
                        output.setframerate(
                            current_format[2]
                        )

                    elif current_format != expected_format:
                        raise TTSError(
                            "Incompatible WAV segments."
                        )

                    frames = source.readframes(
                        source.getnframes()
                    )
                    output.writeframes(frames)
                    total_frames += source.getnframes()

    except (OSError, EOFError, wave.Error):
        raise TTSError(
            "Could not combine Piper WAV segments."
        ) from None

    if expected_format is None or total_frames == 0:
        raise TTSError(
            "Generated audio contains no samples."
        )

    return total_frames / expected_format[2]


def convert_to_mp3(
    wav_path: Path,
    mp3_path: Path,
) -> None:
    ffmpeg = get_settings().FFMPEG_EXECUTABLE

    if shutil.which(ffmpeg) is None:
        raise TTSError(
            "FFmpeg is not installed or "
            "not available on PATH."
        )

    try:
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(wav_path),
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(mp3_path),
            ],
            capture_output=True,
            check=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        raise TTSError(
            "FFmpeg MP3 conversion failed."
        ) from None

    if (
        not mp3_path.is_file()
        or mp3_path.stat().st_size == 0
    ):
        raise TTSError(
            "MP3 conversion produced an empty file."
        )


def generate_audio(
    script: str,
    user_id: int,
    project_id: int,
    language_code: str,
    narrator_name: str,
    audio_format: str,
    synthesizer=None,
    validate_voice: bool = True,
) -> AudioResult:
    """Generate local narration and return stored metadata."""
    settings = get_settings()

    output_format = audio_format.upper()

    if output_format not in {"WAV", "MP3"}:
        raise TTSError(
            "Unsupported audio format."
        )

    model_name, speaker_id = get_voice(
        language_code,
        narrator_name,
    )

    chunks = split_tts_text(
        script,
        settings.PIPER_MAX_CHARS,
    )

    if len(chunks) > settings.PIPER_MAX_CHUNKS:
        raise TTSLimitError(
            "Script exceeds the configured "
            "Piper processing limit."
        )

    # Test injection avoids requiring downloaded models.
    if validate_voice:
        model_path = get_voice_path(model_name)
        verify_speaker(model_path, speaker_id)
    else:
        model_path = (
            settings.PIPER_MODELS_DIR.resolve()
            / f"{model_name}.onnx"
        )

    synthesize = synthesizer or piper_synthesize

    root = settings.AUDIO_STORAGE_ROOT.resolve()

    relative_directory = (
        Path(str(user_id)) / str(project_id)
    )

    output_directory = root / relative_directory
    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    extension = output_format.lower()
    filename = f"{uuid.uuid4().hex}.{extension}"

    relative_path = (
        relative_directory / filename
    )

    destination = root / relative_path

    try:
        with tempfile.TemporaryDirectory() as temp:
            temp_directory = Path(temp)
            wav_path = temp_directory / "combined.wav"

            segments = []

            for chunk in chunks:
                segment = synthesize(
                    chunk,
                    model_path,
                    speaker_id,
                )
                segments.append(segment)

            duration = combine_wav_segments(
                segments,
                wav_path,
            )

            if output_format == "MP3":
                mp3_path = temp_directory / "combined.mp3"
                convert_to_mp3(wav_path, mp3_path)
                source = mp3_path
            else:
                source = wav_path

            shutil.copyfile(source, destination)

        file_size = destination.stat().st_size
        if cloud_enabled():
            upload_path(
                destination,
                audio_key(relative_path.as_posix()),
                "audio/mpeg" if output_format == "MP3" else "audio/wav",
            )

        return AudioResult(
            relative_path=relative_path.as_posix(),
            file_size=file_size,
            duration_seconds=max(1, round(duration)),
            chunks_processed=len(chunks),
        )

    except Exception:
        destination.unlink(missing_ok=True)
        raise


def resolve_audio_path(relative_path: str) -> Path:
    root = get_settings().AUDIO_STORAGE_ROOT.resolve()
    path = (root / relative_path).resolve()

    if not path.is_relative_to(root):
        raise TTSError(
            "Invalid audio storage path."
        )

    if cloud_enabled():
        try:
            ensure_local(audio_key(relative_path), path)
        except (ObjectStorageError, FileNotFoundError) as exc:
            raise TTSError("Stored audio file is missing.") from exc
    elif not path.is_file():
        raise TTSError("Stored audio file is missing.")

    return path


def remove_audio_file(relative_path: str) -> None:
    root = get_settings().AUDIO_STORAGE_ROOT.resolve()
    path = (root / relative_path).resolve()

    if not path.is_relative_to(root):
        raise TTSError(
            "Invalid audio storage path."
        )

    if cloud_enabled():
        delete_object(audio_key(relative_path))
    path.unlink(missing_ok=True)
