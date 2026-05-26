from faster_whisper import WhisperModel
import os

model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")


def transcribe_audio(audio_path: str) -> str:
    segments, _ = model.transcribe(audio_path)
    return " ".join(segment.text for segment in segments)


if __name__ == "__main__":
    sample = os.environ.get("SAMPLE_AUDIO", "call.mp3")
    print(transcribe_audio(sample))
