import importlib.resources as pkg_resources
import json
import os
from pathlib import Path
from tempfile import mkstemp

import numpy as np
import torch
import torchaudio  # type: ignore
import yaml  # type: ignore

import timething
from timething import align  # type: ignore


# yaml file containing all of the models
MODELS_YAML = "models.yaml"


def load_config(
    model: str, k_shingles=5, local_files_only=False, cache_dir=None
) -> align.Config:
    """
    Load config object for the given model key
    """

    text = pkg_resources.read_text(timething, MODELS_YAML)
    cfg = yaml.safe_load(text)
    return align.Config(
        hugging_model=cfg[model]["model"],
        hugging_pin=cfg[model]["pin"],
        sampling_rate=cfg[model]["sampling_rate"],
        language=cfg[model]["language"],
        k_shingles=k_shingles,
        local_files_only=local_files_only,
        cache_dir=cache_dir if cache_dir else align.CACHE_DIR_DEFAULT,
    )


def load_slice(filename: Path, start_seconds: float, end_seconds: float):
    """
    Load an audio slice from a seconds offset and duration using torchaudio.
    """

    info = torchaudio.info(filename)
    num_samples = torchaudio.load(filename)[0].shape[1]
    n_seconds = num_samples / info.sample_rate
    seconds_per_frame = n_seconds / info.num_frames
    start = int(start_seconds / seconds_per_frame)
    end = int(end_seconds / seconds_per_frame)
    duration = end - start
    return torchaudio.load(filename, start, duration)


def load_audio(content: bytes, format: str):
    "Like torchaudio.load, but from a binary blob"

    fd, path = mkstemp(suffix=f".{format}")
    with os.fdopen(fd, "wb") as f:
        f.write(content)
        f.flush()

    audio, sr = torchaudio.load(str(path), format=format)
    os.unlink(path)
    return audio, sr


def alignment_meta(alignment: align.Alignment):
    """Alignment data as a dictionary"""

    def rescale(n_model_frames: float) -> float:
        return alignment.model_frames_to_seconds(n_model_frames)

    def alignments(segments):
        return [
            {
                "label": segment.label,
                "start": rescale(segment.start),
                "end": rescale(segment.end),
                "score": segment.score,
            }
            for segment in segments
        ]

    # combine the metadata
    return {
        "id": alignment.id,
        "n_model_frames": alignment.n_model_frames,
        "n_audio_samples": alignment.n_audio_samples,
        "sampling_rate": alignment.sampling_rate,
        "partition_score": alignment.partition_score,
        "recognised": alignment.recognised,
        "chars": alignments(alignment.chars),
        "chars_cleaned": alignments(alignment.chars_cleaned),
        "words": alignments(alignment.words),
        "words_cleaned": alignments(alignment.words_cleaned),
    }


def write_alignment(output_path: Path, id: str, alignment: align.Alignment):
    """
    Write a custom json alignments file for a given aligned recording.
    """

    # grab the metadata
    meta = alignment_meta(alignment)

    # write any path components, e.g. for id 'audio/one.mp3.json'
    filename = alignment_filename(output_path, id)
    filename.parent.mkdir(parents=True, exist_ok=True)

    # write the file
    with open(filename, "w", encoding="utf8") as f:
        f.write(json.dumps(meta, indent=4, ensure_ascii=False))


def read_alignment(alignments_dir: Path, alignment_id: str) -> align.Alignment:
    """
    Read Aligments json file.
    """

    with open(alignment_filename(alignments_dir, alignment_id), "r") as f:
        alignment_dict = json.load(f)

    alignment = align.Alignment(
        alignment_dict["id"],
        np.array([]),  # log probs
        alignment_dict["recognised"],  # recognised string
        np.array([]),  # trellis
        np.array([]),  # backtracking path
        [],  # char segments
        [],  # original char segments
        [],  # word segments
        [],  # original word segments
        alignment_dict["n_model_frames"],
        alignment_dict["n_audio_samples"],
        alignment_dict["sampling_rate"],
        alignment_dict["partition_score"],
    )

    def rescale(n_seconds: int) -> int:
        return alignment.seconds_to_model_frames(n_seconds)

    def dict_to_segment(d: dict) -> align.Segment:
        return align.Segment(
            start=rescale(d["start"]),
            end=rescale(d["end"]),
            label=d["label"],
            score=d["score"],
        )

    alignment.chars_cleaned = [
        dict_to_segment(d) for d in alignment_dict["chars_cleaned"]
    ]

    alignment.chars = [dict_to_segment(d) for d in alignment_dict["chars"]]

    alignment.words_cleaned = [
        dict_to_segment(d) for d in alignment_dict["words_cleaned"]
    ]

    alignment.words = [dict_to_segment(d) for d in alignment_dict["words"]]

    return alignment


def alignment_filename(path, id):
    """
    From audio/one.mp3 to audio/one.mp3.json
    """

    filename = path / id
    return filename.parent / (filename.name + ".json")


# Gpu


def best_device():
    if gpu_cuda_available():
        return torch.device("cuda")
    else:
        return torch.device("cpu")


def gpu_mps_available():
    return torch.backends.mps.is_available() and torch.backends.mps.is_built()

def cuda_is_built():
    if hasattr(torch.cuda, "is_built"):
        return torch.cuda.is_built()
    else:
        # Fallback: assume CUDA is built if torch.version.cuda is not None.
        return True

def gpu_cuda_available():
    return torch.cuda.is_available() and cuda_is_built()
