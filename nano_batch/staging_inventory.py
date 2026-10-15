"""Bounded staging metadata inspection; no recovery, media reads, or writes.

Only this module's allowlisted report leaves the inspection boundary. JSON parsing
does read embedded prompts into memory; no raw state or exception is returned.
"""

from __future__ import annotations

import json
import os
import re
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from .downloads import ImageValidationError, SUPPORTED_IMAGE_FORMATS, canonical_extension_for_image_format
from .models import FlowProjectReference, PendingDownloadCommit, VALID_DOWNLOAD_STATUSES, VALID_STATUSES
from .runner import output_basename


MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_STATE_ITEMS = 2048
MAX_ASSET_ENTRIES = 512
MAX_STAGING_ENTRIES = 64
MAX_FILES_PER_STAGING = 64
MAX_CANONICAL_ENTRIES = 256
_STAGING = re.compile(r"\.staging-[a-z0-9_-]+")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_REPARSE = 0x400


class _Unsafe(ValueError):
    pass


class _Changed(ValueError):
    pass


def _redirected(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def _fingerprint(info):
    # Access times can change as an OS consequence of reading; never use or claim
    # preservation of them. These fields detect changes, not writer inactivity.
    return tuple(getattr(info, key) for key in (
        "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink",
    )) + (getattr(info, "st_file_attributes", 0),)


def _identity(info):
    return (info.st_dev, info.st_ino) if info.st_ino else None


@dataclass
class _Directory:
    path: Path
    fd: int | None = None


@contextmanager
def _windows_handle(path, *, read_state=False):
    """Open the named object itself, without following its final reparse point.

    Directory handles omit FILE_SHARE_DELETE. Held ancestry prevents rename/link
    replacement while pathname-based metadata enumeration uses those directories.
    Directory-list access is required for Windows sharing enforcement; attribute-
    only handles do not prevent replacement. No write/delete access is requested.
    """
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    attributes = kernel.GetFileInformationByHandleEx
    attributes.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    attributes.restype = wintypes.BOOL
    final_name = kernel.GetFinalPathNameByHandleW
    final_name.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    final_name.restype = wintypes.DWORD
    handle = create(str(path), 0x80000000 if read_state else 0x81, 3, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        tag = (wintypes.DWORD * 2)()
        if not attributes(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)):
            raise ctypes.WinError(ctypes.get_last_error())
        if tag[0] & _REPARSE or bool(tag[0] & 0x10) == read_state:
            raise _Unsafe()
        buffer = ctypes.create_unicode_buffer(32768)
        count = final_name(handle, buffer, len(buffer), 0)
        if not count or count >= len(buffer):
            raise _Unsafe()
        actual = buffer.value
        if actual.startswith("\\\\?\\"):
            actual = actual[4:]
        if os.path.normcase(actual) != os.path.normcase(str(path)):
            raise _Unsafe()  # Includes short-name/other ambiguous aliases.
        yield handle
    finally:
        close(handle)


@contextmanager
def _directory(path, parent=None):
    if os.name == "nt":
        with _windows_handle(path):
            yield _Directory(path)
    elif os.name == "posix" and hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            fd = os.open(path if parent is None else path.name, flags,
                         **({} if parent is None else {"dir_fd": parent.fd}))
        except OSError:
            # Distinguish a visible link from an ordinary permission failure.
            info = path.lstat() if parent is None else _stat_entry(parent, path.name)
            if _redirected(info):
                raise _Unsafe() from None
            raise
        try:
            yield _Directory(path, fd)
        finally:
            os.close(fd)
    else:
        raise _Unsafe()


def _stat_entry(directory, name):
    if not name or name in {".", ".."} or any(ch in name for ch in "/\\:\0"):
        raise _Unsafe()
    if directory.fd is not None:
        return os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
    return (directory.path / name).lstat()


def _entry_names(directory, limit):
    names = []
    with os.scandir(directory.fd if directory.fd is not None else directory.path) as entries:
        for entry in entries:
            if len(names) == limit:
                return sorted(names), True
            names.append(entry.name)
    return sorted(names), False


def _read_state_bytes(directory):
    info = _stat_entry(directory, "run_state.json")
    if _redirected(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise _Unsafe()
    if info.st_size > MAX_STATE_BYTES:
        return None, "oversized", _fingerprint(info)
    if directory.fd is not None:
        fd = os.open("run_state.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory.fd)
        with os.fdopen(fd, "rb") as handle:
            if _fingerprint(os.fstat(handle.fileno())) != _fingerprint(info):
                raise _Changed()
            data = handle.read(MAX_STATE_BYTES + 1)
            if _fingerprint(os.fstat(handle.fileno())) != _fingerprint(info):
                raise _Changed()
    else:
        import ctypes
        from ctypes import wintypes
        with _windows_handle(directory.path / "run_state.json", read_state=True) as handle:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)

            class FileInfo(ctypes.Structure):
                _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
                            ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
                            ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                            ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                            ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

            get_info = kernel.GetFileInformationByHandle
            get_info.argtypes, get_info.restype = [wintypes.HANDLE, ctypes.POINTER(FileInfo)], wintypes.BOOL
            actual = FileInfo()
            if not get_info(handle, ctypes.byref(actual)):
                raise ctypes.WinError(ctypes.get_last_error())
            if actual.attributes & (0x10 | _REPARSE) or actual.links != 1:
                raise _Unsafe()
            class FileId(ctypes.Structure):
                _fields_ = [("volume", ctypes.c_ulonglong), ("file_id", ctypes.c_ubyte * 16)]

            get_id = kernel.GetFileInformationByHandleEx
            get_id.argtypes, get_id.restype = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL
            file_id = FileId()
            if not get_id(handle, 18, ctypes.byref(file_id), ctypes.sizeof(file_id)):
                raise ctypes.WinError(ctypes.get_last_error())
            # Python 3.12 stat uses the full volume serial and 128-bit file ID.
            identity = (file_id.volume, int.from_bytes(file_id.file_id, "little"))
            if (_identity(info) != identity or
                    info.st_size != ((actual.size_high << 32) | actual.size_low)):
                raise _Changed()  # Refuse replacement before reading any state bytes.
            read = kernel.ReadFile
            read.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            read.restype = wintypes.BOOL
            buffer, count = ctypes.create_string_buffer(MAX_STATE_BYTES + 1), wintypes.DWORD()
            if not read(handle, buffer, len(buffer), ctypes.byref(count), None):
                raise ctypes.WinError(ctypes.get_last_error())
            data = buffer.raw[:count.value]  # A short/inconsistent read fails below.
    if _fingerprint(_stat_entry(directory, "run_state.json")) != _fingerprint(info):
        raise _Changed()
    if len(data) <= MAX_STATE_BYTES and len(data) != info.st_size:
        raise _Changed()
    return data, "oversized" if len(data) > MAX_STATE_BYTES else "available", _fingerprint(info)


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _positive(value):
    return type(value) is int and 0 < value <= 999999


def _relative_claim(value, root):
    if not isinstance(value, str) or not value or "\0" in value:
        raise _Unsafe()
    path = Path(value)
    if ".." in path.parts or (path.drive and not path.is_absolute()):
        raise _Unsafe()
    if path.is_absolute():
        path = path.relative_to(root)  # Lexical only: never resolve saved paths.
    if path.parts[0:1] != ("assets",) or any(
        part in {".", ".."} or any(ch in part for ch in ':*?"<>|') for part in path.parts
    ):
        raise _Unsafe()
    if not (len(path.parts) == 2 or (len(path.parts) == 3 and _STAGING.fullmatch(path.parts[1]))):
        raise _Unsafe()
    if len(path.parts) == 2 and path.name.casefold().startswith(".staging-"):
        raise _Unsafe()  # A staging directory is not a saved output file.
    return path.as_posix()


def _claim_key(path):
    path = path.casefold() if os.name == "nt" else path
    # Canonical extensions compete for one deterministic stem.
    return path.rsplit(".", 1)[0] if len(Path(path).parts) == 2 else path


def _state_metadata(data, root):
    """Extract only internal references. None of these private strings is output."""
    raw = json.loads(data, object_pairs_hook=_json_object)
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise ValueError()
    if (raw.get("schema") != "nano-banana-batch-run-state" or type(raw.get("version")) is not int
            or not isinstance(raw.get("batch_id"), str) or not raw["batch_id"]):
        raise ValueError()
    if len(raw["items"]) > MAX_STATE_ITEMS:
        return [], True, True, {}
    rows, claims, outputs, uuids, orders = [], {}, [], {}, set()
    bad = False
    project = None
    try:
        if raw.get("flow_project") is not None:
            project = FlowProjectReference(**raw["flow_project"])
            project.validate()
            if raw.get("flow_project_required") is not True:
                bad = True
    except (TypeError, ValueError):
        bad = True

    def claim(path, owner):
        claims.setdefault(_claim_key(path), set()).add(owner)

    for index, item in enumerate(raw["items"]):
        if not isinstance(item, dict) or not _positive(item.get("order")):
            bad = True
            continue
        if item["order"] in orders:
            bad = True
        if (not isinstance(item.get("status"), str) or item["status"] not in VALID_STATUSES
                or not isinstance(item.get("download_status"), str) or item["download_status"] not in VALID_DOWNLOAD_STATUSES):
            bad = True
        orders.add(item["order"])
        candidates, saved_outputs = item.get("candidates", []), item.get("output_files", [])
        if (not isinstance(candidates, list) or len(candidates) > 2 or
                not isinstance(saved_outputs, list) or len(saved_outputs) > 8):
            bad = True
            continue
        variants = set()
        for offset, candidate in enumerate(candidates):
            owner = (index, offset)
            if not isinstance(candidate, dict) or candidate.get("variant") not in (1, 2) or type(candidate.get("variant")) is not int:
                bad = True
                continue
            if candidate["variant"] in variants:
                bad = True
            variants.add(candidate["variant"])
            row = {"owner": owner, "order": item["order"], "variant": candidate["variant"],
                   "canonical": None, "stage": None, "pending_size": None,
                   "completed": candidate.get("download_status") == "completed", "bad": False}
            if (not isinstance(candidate.get("download_status"), str)
                    or candidate["download_status"] not in VALID_DOWNLOAD_STATUSES):
                row["bad"] = True
            candidate_id = candidate.get("candidate_id")
            if isinstance(candidate_id, str) and _UUID.fullmatch(candidate_id):
                uuids.setdefault(candidate_id, []).append(row)
            else:
                row["bad"] = True  # Legacy/missing identity is not independent proof.
            local = []
            for field in ("local_path", "local_relative_path"):
                if candidate.get(field) is not None:
                    try:
                        path = _relative_claim(candidate[field], root)
                        if field == "local_relative_path" and Path(candidate[field]).is_absolute():
                            raise _Unsafe()
                        local.append(path)
                        claim(path, owner)
                    except (ValueError, OSError):
                        bad = True
            if len(set(local)) > 1:
                row["bad"] = True
            if row["completed"]:
                try:
                    expected = "assets/" + output_basename(item["order"], item["visual_id"], candidate["variant"],
                        canonical_extension_for_image_format(candidate["image_format"]))
                    if (not local or set(local) != {expected} or candidate.get("suggested_filename") != Path(expected).name
                            or not isinstance(candidate.get("sha256"), str)
                            or not re.fullmatch(r"[0-9a-f]{64}", candidate["sha256"])
                            or any(type(candidate.get(field)) is not int or candidate[field] <= 0
                                   for field in ("byte_size", "width", "height"))
                            or candidate.get("mime_type") != SUPPORTED_IMAGE_FORMATS[candidate["image_format"]][0]
                            or not isinstance(candidate.get("downloaded_at"), str) or not candidate["downloaded_at"]
                            or item.get("status") != "completed" or candidate.get("download_error") is not None):
                        raise ValueError()
                    row["canonical"] = expected
                except (ValueError, ImageValidationError, KeyError, TypeError, AttributeError):
                    row["bad"] = True
            if candidate.get("pending_download_commit") is not None:
                try:
                    pending = PendingDownloadCommit(**candidate["pending_download_commit"])
                    pending.validate()
                    expected = "assets/" + output_basename(item["order"], item["visual_id"], candidate["variant"],
                        canonical_extension_for_image_format(pending.image_format))
                    staged = _relative_claim(pending.staging_relative_path, root)
                    if (pending.canonical_relative_path != expected or
                            not re.fullmatch(r"assets/\.staging-[a-z0-9_-]+/v%02d\.download" % candidate["variant"], staged)):
                        raise ValueError()
                    row.update(canonical=expected, stage=staged, pending_size=pending.byte_size)
                    claim(expected, owner)
                    claim(staged, owner)
                    if (raw["version"] < 6 or raw.get("flow_project_required") is not True or project is None
                            or pending.project_id != project.project_id or pending.batch_id != raw["batch_id"]
                            or pending.candidate_id != candidate_id or pending.order != item["order"]
                            or pending.visual_id != item["visual_id"] or pending.variant != candidate["variant"]
                            or pending.generation_attempt_count != item.get("attempt_count")
                            or pending.download_attempt_count != item.get("download_attempt_count")
                            or type(item.get("attempt_count")) is not int or type(item.get("download_attempt_count")) is not int
                            or item.get("status") != "completed" or not item.get("completed_at")
                            or item.get("download_status") != "running" or candidate.get("download_status") != "running"
                            or any(item.get(field) is not None for field in ("error", "generation_error_code", "download_error", "download_completed_at"))
                            or candidate.get("download_error") is not None or local
                            or any(candidate.get(field) is not None for field in (
                                "sha256", "byte_size", "width", "height", "image_format", "mime_type", "downloaded_at"))):
                        row["bad"] = True
                except (TypeError, ValueError, KeyError, AttributeError):
                    bad = row["bad"] = True
            rows.append(row)
        for number, value in enumerate(saved_outputs):
            try:
                path = _relative_claim(value, root)
                matches = [row for row in rows if row["owner"][0] == index and row["completed"]
                           and row["pending_size"] is None and row["canonical"] == path]
                owner = matches[0]["owner"] if len(matches) == 1 else ("output", index, number)
                if (index, path) in outputs:
                    bad = True
                outputs.append((index, path))
                claim(path, owner)
            except (ValueError, OSError):
                bad = True
    for matches in uuids.values():
        if len(matches) > 1:
            for row in matches:
                row["bad"] = True
    for row in rows:
        if any(len(claims.get(_claim_key(path), ())) > 1 for path in (row["canonical"], row["stage"]) if path):
            row["bad"] = True
    staging_claims = {}
    for path, owners in claims.items():
        parts = Path(path).parts
        if len(parts) == 3:
            staging_claims.setdefault(parts[1], set()).update(owners)
    return rows, bad or any(row["bad"] for row in rows), False, staging_claims


def _new_report():
    return {"schema": "nano-banana-staging-inventory-v1", "read_only": True,
            "cleanup_authorized": False, "recovery_eligibility": "not_evaluated",
            "content_integrity": "not_checked", "run_status": "not_inspected",
            "state_status": "not_inspected", "assets_status": "not_inspected",
            "inspection_status": "incomplete", "truncated": False, "entries": [], "issues": [],
            "limitations": ["Informational only; this tool does not delete files.",
                "Results are not cleanup authorization; apparently orphaned does not mean disposable.",
                "Recovery eligibility remains determined by normal Resume with independent Batch inputs.",
                "Consistency covers inspected saved metadata only, not independent Batch or image validation.",
                "State JSON parsing reads embedded prompts in memory; they are never reported.",
                "Metadata snapshots do not prove content integrity, writer inactivity, or power-loss safety.",
                "Filesystem changes after inspection and provider behavior remain TOCTOU limitations."],
            "limits": {"state_bytes": MAX_STATE_BYTES, "state_items": MAX_STATE_ITEMS,
                "asset_entries": MAX_ASSET_ENTRIES, "staging_entries": MAX_STAGING_ENTRIES,
                "files_per_staging": MAX_FILES_PER_STAGING, "canonical_entries": MAX_CANONICAL_ENTRIES}}


def _status(error):
    if isinstance(error, FileNotFoundError):
        return "missing"
    if isinstance(error, _Unsafe):
        return "unsafe"
    if isinstance(error, _Changed):
        return "changed"
    return "unreadable"


def _local_windows_drive(anchor):
    import ctypes
    from ctypes import wintypes

    drive_type = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
    drive_type.argtypes, drive_type.restype = [wintypes.LPCWSTR], wintypes.UINT
    return drive_type(anchor) in {2, 3, 6}  # Removable, fixed, or RAM; never mapped network drives.


def _inspect_entry(assets, name, rows, canonical, state_status, staging_claims):
    key = os.path.normcase(name)
    references = [row for row in rows if row["stage"] and os.path.normcase(Path(row["stage"]).parts[1]) == key]
    result = {"entry_id": "", "labels": [], "status": "observed", "state_consistency": "insufficient",
              "owners": [], "files_observed": 0, "partial_downloads_observed": 0,
              "shared_files_observed": 0, "canonical_links_observed": 0,
              "canonical_metadata_present": any(row["canonical"] in canonical for row in references),
              "issues": []}
    labels, issues, owners = set(), set(), set()
    if references:
        labels.update(("A", "B"))
    if state_status not in {"available", "inconsistent"}:
        issues.add("state_unavailable")
    conflict = state_status == "inconsistent" or any(row["bad"] for row in references)
    receipt_owners = {row["owner"] for row in references}
    if staging_claims.get(key, set()) - receipt_owners:
        labels.add("C")
        conflict = True
        issues.add("nonreceipt_staging_claim")
    for row in references:
        owners.add((row["order"], row["variant"]))
        if row["canonical"] not in canonical:
            issues.add("canonical_file_missing")
        elif canonical[row["canonical"]].st_size != row["pending_size"]:
            issues.add("canonical_size_mismatch")
            conflict = True
    try:
        before = _stat_entry(assets, name)
        if not _STAGING.fullmatch(name) or _redirected(before) or not stat.S_ISDIR(before.st_mode):
            raise _Unsafe()
        with _directory(assets.path / name, assets) as directory:
            names, truncated = _entry_names(directory, MAX_FILES_PER_STAGING)
            if truncated:
                issues.add("entries_truncated")
            seen = set()
            for filename in names:
                try:
                    info = _stat_entry(directory, filename)
                    if _redirected(info):
                        raise _Unsafe()
                    if stat.S_ISDIR(info.st_mode):
                        issues.add("nested_directory_skipped")
                        conflict = True
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        raise _Unsafe()
                    result["files_observed"] += 1
                    result["partial_downloads_observed"] += filename.casefold().endswith(".crdownload")
                    result["shared_files_observed"] += info.st_nlink > 1
                    seen.add(filename)
                    associated = [row for row in rows if row["canonical"] in canonical and info.st_nlink > 1
                                  and canonical[row["canonical"]].st_nlink > 1
                                  and _identity(info) is not None
                                  and _identity(canonical[row["canonical"]]) == _identity(info)]
                    if associated:
                        result["canonical_links_observed"] += 1
                        result["canonical_metadata_present"] = True
                    if info.st_nlink > 1 and not associated:
                        conflict = True
                        issues.add("unresolved_shared_file")
                    for row in references + associated:
                        owners.add((row["order"], row["variant"]))
                        conflict |= row["bad"]
                        if row["completed"] and row["pending_size"] is None:
                            labels.add("D")
                    for row in references:
                        if Path(row["stage"]).name == filename and info.st_size != row["pending_size"]:
                            conflict = True
                            issues.add("staging_size_mismatch")
                    if _fingerprint(_stat_entry(directory, filename)) != _fingerprint(info):
                        raise _Changed()
                except (OSError, ValueError) as error:
                    conflict = True
                    issues.add({"unsafe": "unsafe_entry", "changed": "entry_changed",
                                "missing": "entry_missing"}.get(_status(error), "entry_unreadable"))
            for row in references:
                if Path(row["stage"]).name not in seen:
                    issues.add("staging_file_missing")
            if _fingerprint(_stat_entry(assets, name)) != _fingerprint(before):
                raise _Changed()
    except (OSError, ValueError) as error:
        result["status"] = _status(error)
        conflict |= result["status"] != "missing"
    owners.update((row["order"], row["variant"]) for row in references)
    if not labels:
        labels.add("E")
    if conflict:
        labels.add("C")
    result["labels"], result["issues"] = sorted(labels), sorted(issues)
    result["owners"] = [{"order": order, "variant": variant} for order, variant in sorted(owners)[:8]]
    if len(owners) > 8:
        result["issues"].append("owners_truncated")
    result["state_consistency"] = "conflicting" if conflict else (
        "consistent" if references and state_status == "available" else "insufficient")
    return result


def _inspect(root, directory, report):
    rows, staging_claims, state_fingerprint = [], {}, None
    try:
        data, report["state_status"], state_fingerprint = _read_state_bytes(directory)
        if report["state_status"] == "available":
            try:
                rows, bad, truncated, staging_claims = _state_metadata(data, root)
                report["truncated"] |= truncated
                report["state_status"] = "inconsistent" if bad else "available"
            except (ValueError, TypeError, RecursionError, UnicodeError):
                report["state_status"] = "malformed"
        del data  # Release the bounded raw state before filesystem enumeration.
    except (ValueError, OSError) as error:
        report["state_status"] = _status(error)
    canonical = {}
    try:
        assets_before = _stat_entry(directory, "assets")
        if _redirected(assets_before) or not stat.S_ISDIR(assets_before.st_mode):
            raise _Unsafe()
        with _directory(root / "assets", directory) as assets:
            report["assets_status"] = "observed"
            names, truncated = _entry_names(assets, MAX_ASSET_ENTRIES)
            report["truncated"] |= truncated
            canonical_rows = [row for row in rows if row["canonical"]]
            if len(canonical_rows) > MAX_CANONICAL_ENTRIES:
                report["truncated"] = True
                report["state_status"] = "inconsistent"
            for row in canonical_rows[:MAX_CANONICAL_ENTRIES]:
                try:
                    info = _stat_entry(assets, Path(row["canonical"]).name)
                    if _redirected(info) or not stat.S_ISREG(info.st_mode):
                        raise _Unsafe()
                    canonical[row["canonical"]] = info
                except FileNotFoundError:
                    pass
                except (ValueError, OSError):
                    row["bad"] = True
                    report["issues"].append("canonical_evidence_unavailable")
            # Keep observed spelling so noncanonical names are rejected, while
            # deduplicating Windows case aliases of a saved metadata reference.
            staging = {os.path.normcase(name): name for name in names
                       if name.casefold().startswith(".staging-")}
            references = [Path(row["stage"]).parts[1] for row in rows if row["stage"]]
            for name in references + list(staging_claims):
                staging.setdefault(os.path.normcase(name), name)
            if len(staging) > MAX_STAGING_ENTRIES:
                report["truncated"] = True
            for name in sorted(staging.values())[:MAX_STAGING_ENTRIES]:
                entry = _inspect_entry(assets, name, rows, canonical, report["state_status"], staging_claims)
                entry["entry_id"] = "staging-%03d" % (len(report["entries"]) + 1)
                report["entries"].append(entry)
                report["truncated"] |= any(issue.endswith("truncated") for issue in entry["issues"])
            for path, before in canonical.items():
                try:
                    if _fingerprint(_stat_entry(assets, Path(path).name)) != _fingerprint(before):
                        raise _Changed()
                except (ValueError, OSError):
                    raise _Changed() from None
            if _fingerprint(_stat_entry(directory, "assets")) != _fingerprint(assets_before):
                raise _Changed()
    except (ValueError, OSError) as error:
        report["assets_status"] = _status(error)
    if state_fingerprint is not None:
        try:
            if _fingerprint(_stat_entry(directory, "run_state.json")) != state_fingerprint:
                report["state_status"] = "changed"
        except (ValueError, OSError):
            report["state_status"] = "changed"
    if report["state_status"] == "changed" or report["assets_status"] == "changed":
        for entry in report["entries"]:
            entry["labels"] = sorted(set(entry["labels"]) | {"C"})
            entry["state_consistency"] = "conflicting"
            entry["issues"].append("snapshot_changed")
    if (report["state_status"] in {"available", "inconsistent"}
            and report["assets_status"] in {"observed", "missing"} and not report["truncated"]
            and not report["issues"]
            and all(entry["status"] in {"observed", "missing"} and not any(
                issue in entry["issues"] for issue in (
                    "entry_changed", "entry_missing", "entry_unreadable", "unsafe_entry", "nested_directory_skipped")
            ) for entry in report["entries"])):
        report["inspection_status"] = "complete"


def inspect_staging(run_dir: Path) -> dict:
    """Inspect one explicit run. Return only bounded, fixed-vocabulary metadata."""
    report = _new_report()
    try:
        selected = Path(run_dir)
        if ".." in selected.parts or (selected.drive and not selected.is_absolute()):
            raise _Unsafe()
        root = selected.absolute()
        if os.name == "nt" and (not re.fullmatch(r"[A-Za-z]:\\", root.anchor)
                                or not _local_windows_drive(root.anchor)):
            raise _Unsafe()  # No device paths, UNC roots, or mapped network drives.
        if len(root.parts) > 64:
            raise _Unsafe()
        with ExitStack() as guards:
            directory = guards.enter_context(_directory(Path(root.anchor)))
            for part in root.parts[1:]:
                if part in {".", ".."} or any(ch in part for ch in ':*?"<>|\0'):
                    raise _Unsafe()
                directory = guards.enter_context(_directory(directory.path / part, directory))
            report["run_status"] = "observed"
            _inspect(root, directory, report)
    except (OSError, ValueError) as error:
        report["run_status"] = _status(error)
    except Exception:
        # Never let raw parser/provider errors reach the CLI's legacy formatter.
        report["issues"].append("inspection_unavailable")
        report["inspection_status"] = "incomplete"
    return report
