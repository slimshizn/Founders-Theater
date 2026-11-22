"""Presentation of Silo FileVersion and WatchRollup catalog responses.

Field names and units follow internal/apiv2/catalog_items.go in Silo Server:
file_id is opaque, file_size is bytes, bitrate is kbps, progress is seconds.
This module deliberately has no Kodi dependency.
"""

import math
import re


def available_versions(item):
    result, seen = [], set()
    for version in item.get("versions") or []:
        if not isinstance(version, dict):
            continue
        file_id = version.get("file_id")
        if not isinstance(file_id, str) or not file_id or file_id in seen:
            continue
        seen.add(file_id)
        result.append(version)
    return result


def default_version(item, mode="ask", preferred_file_id=None):
    """Keep an explicit selection; otherwise honor the default-file policy."""
    versions = available_versions(item)
    if not versions:
        return None
    if preferred_file_id is not None:
        selected = next((version for version in versions if version["file_id"] == preferred_file_id), None)
        if selected:
            return selected
    if mode != "first":
        last_file_id = (item.get("user_data") or {}).get("last_file_id")
        selected = next((version for version in versions if version["file_id"] == last_file_id), None)
        if selected:
            return selected
    return versions[0]


def _first_video_track(version):
    tracks = [track for track in (version or {}).get("video_tracks") or [] if isinstance(track, dict)]
    return tracks[0] if tracks else {}


def _resolution_class(version):
    value = (version or {}).get("resolution") or _first_video_track(version).get("resolution")
    value = str(value or "").strip().lower()
    return {"4k": "2160p", "uhd": "2160p", "fhd": "1080p", "hd": "720p", "sd": "480p"}.get(value, value)


def _height(version):
    """Picture lines of a version's resolution class ("1080p" is 1080), or 0 when unknown."""
    numbers = re.findall(r"\d{3,4}", _resolution_class(version))
    return int(numbers[-1]) if numbers else 0


def _dynamic_range(version):
    kind = str(_first_video_track(version).get("video_range_type") or "").upper()
    if "DOVI" in kind or "DOLBY" in kind:
        return "dv"
    if "HDR" in kind or "HLG" in kind or (version or {}).get("hdr") is True:
        return "hdr"
    return "sdr"


def _kbps(version):
    """A version's bitrate in kbps, worked out from size and length when Silo sends none."""
    rate = _positive_number(version.get("bitrate"))
    if rate:
        return rate
    size, seconds = _positive_number(version.get("file_size")), _positive_number(version.get("duration"))
    return size * 8 / seconds / 1000 if size and seconds else 0.0


def matching_version(previous, versions):
    """The file_id of the version that continues ``previous`` on another episode, or None.

    Like Silo's Android TV app, the next episode keeps the resolution class,
    preferring the same video codec, then the same HDR or Dolby Vision kind,
    then the same container. That app stops at a tie; here the choice goes on
    so Play next never has to ask: the same edition, the same audio codec, the
    bitrate closest to the one just played (two releases at one resolution
    differ mostly there), the first part of a multi-part file, then catalog
    order. With no version at that resolution the nearest one is used, the
    lower of two equally near. None when there are no versions or nothing
    tells how ``previous`` compares with them.
    """
    previous = previous if isinstance(previous, dict) else {}
    versions = [version for version in versions or [] if isinstance(version, dict) and version.get("file_id")]
    resolution = _resolution_class(previous)
    if not resolution or not versions:
        return None
    candidates = [version for version in versions if _resolution_class(version) == resolution]
    if not candidates:
        height = _height(previous)
        sized = [version for version in versions if _height(version)]
        if not height or not sized:
            return None
        nearest = min((abs(_height(version) - height), _height(version)) for version in sized)
        candidates = [version for version in sized if (abs(_height(version) - height), _height(version)) == nearest]
    played = _kbps(previous)

    def token(version, key, track_key=None):
        value = version.get(key) or (_first_video_track(version).get(track_key) if track_key else None)
        return str(value or "").strip().lower()

    def distance(entry):
        index, version = entry
        rate = _kbps(version)
        part = version.get("presentation_part_index")
        return (token(version, "codec_video", "codec") != token(previous, "codec_video", "codec"),
                _dynamic_range(version) != _dynamic_range(previous),
                token(version, "container") != token(previous, "container"),
                token(version, "edition_key") != token(previous, "edition_key"),
                token(version, "codec_audio") != token(previous, "codec_audio"),
                abs(math.log(rate / played)) if rate and played else math.inf,
                part if isinstance(part, int) and not isinstance(part, bool) and part > 0 else 0,
                index)

    return min(enumerate(candidates), key=distance)[1]["file_id"]


def version_name(version):
    """The file's name, or "" when Silo keeps it back.

    Silo sends file_name and file_path only to the acting admin and to accounts that may
    curate metadata; everyone else gets the technical details without them.
    """
    filename = version.get("file_name") or version.get("file_path")
    if filename:
        return str(filename).replace("\\", "/").rsplit("/", 1)[-1]
    return ""


def _positive_number(value):
    if isinstance(value, bool):
        return 0.0
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def version_label(version):
    parts = []
    for key in ("resolution", "edition_raw", "codec_video", "codec_audio", "container"):
        value = version.get(key)
        if value:
            parts.append(str(value).upper() if key.startswith("codec_") or key == "container" else str(value))
    if not version.get("edition_raw") and version.get("edition_key"):
        parts.insert(1 if parts else 0, str(version["edition_key"]).replace("_", " "))
    if "hdr" in version:
        parts.insert(1 if parts else 0, "HDR" if version["hdr"] else "SDR")
    size = _positive_number(version.get("file_size"))
    if size:
        parts.append("{:.1f} GiB".format(size / (1024 ** 3)))
    bitrate = _positive_number(version.get("bitrate"))
    if bitrate:
        parts.append("{:.1f} Mbps".format(bitrate / 1000))
    part_index, part_total = version.get("presentation_part_index"), version.get("presentation_part_total")
    if isinstance(part_index, int) and isinstance(part_total, int) and part_total > 1:
        parts.append("Part {} of {}".format(part_index, part_total))
    return " · ".join(parts) or version_name(version) or "Media file"


def subtitle_choices(version):
    """Return ``(playback_index, label)`` for each subtitle of a file version.

    The catalog's ``subtitle_tracks[].index`` is a stream number, not the
    playback ordinal. Playback numbers external files first (0..E-1, catalog
    order), then embedded tracks (E.., catalog order), so that is the index a
    start request names. An unknown index makes Silo play without subtitles.
    """
    tracks = [track for track in (version or {}).get("subtitle_tracks") or [] if isinstance(track, dict)]
    external = [track for track in tracks if track.get("external")]
    embedded = [track for track in tracks if not track.get("external")]
    numbered = list(enumerate(external)) + [(len(external) + i, track) for i, track in enumerate(embedded)]
    choices = []
    for index, track in numbered:
        label = str(track.get("language") or "Unknown").upper()
        title = track.get("title") or track.get("embedded_title")
        if title:
            label += " · " + str(title)
        flags = [name for key, name in (("forced", "Forced"), ("hearing_impaired", "SDH")) if track.get(key)]
        if track.get("external"):
            flags.append("External file")
        if flags:
            label += " (" + ", ".join(flags) + ")"
        choices.append((index, label))
    return choices


def is_multipart(version):
    if not version:
        return False
    return (version.get("presentation_kind") in ("multipart", "multipart_movie", "split_episode")
            or _positive_number(version.get("presentation_part_index")) > 0
            or _positive_number(version.get("presentation_part_total")) > 1)


def resume_position(item):
    progress = item.get("user_data") or {}
    if progress.get("played") and not progress.get("is_in_progress"):
        return 0.0
    position = _positive_number(progress.get("position_seconds", item.get("position_seconds")))
    duration = _positive_number(progress.get("duration_seconds", item.get("duration_seconds")))
    return 0.0 if duration and position >= duration else position


def format_time(seconds):
    seconds = int(_positive_number(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return "{}:{:02d}:{:02d}".format(hours, minutes, seconds) if hours else "{}:{:02d}".format(minutes, seconds)
