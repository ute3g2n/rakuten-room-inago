"""Race-resistant, no-reparse file access below a host-owned directory."""

from __future__ import annotations

import ctypes
from functools import wraps
from types import GeneratorType
import os
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Iterator, Literal

from automation.inago.maintenance.failure_journal import (
    record_current_operation_failure,
)


class SafeFileError(OSError):
    """A path could not be opened without following a reparse point."""


def _trace_safe_file_operation(function):
    """Capture failures at the safe-file boundary without masking the cause."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            record_current_operation_failure(
                f"safe_file.{function.__name__}", exc
            )
            raise
        if isinstance(result, GeneratorType):
            def observed_generator():
                try:
                    yield from result
                except Exception as exc:
                    record_current_operation_failure(
                        f"safe_file.{function.__name__}", exc
                    )
                    raise

            return observed_generator()
        return result

    return wrapped


@contextmanager
@_trace_safe_file_operation
def open_file_beneath(
    root: str | Path,
    relative_path: str,
    mode: Literal["rb", "xb"],
) -> Iterator[BinaryIO]:
    """Open one file relative to a pinned root without following path aliases.

    Directory components are opened one at a time. On Windows, native
    handle-relative opens use OBJ_DONT_REPARSE and FILE_OPEN_REPARSE_POINT;
    on POSIX, openat-style dir_fd operations use O_NOFOLLOW.
    """

    parts = PurePosixPath(relative_path).parts
    if (
        not parts
        or any(part in {"", ".", ".."} or "/" in part or "\\" in part for part in parts)
        or mode not in {"rb", "xb"}
    ):
        raise SafeFileError("safe_relative_path_invalid")
    if os.name == "nt":
        with _open_windows_beneath(Path(root), parts, mode) as stream:
            yield stream
        return
    with _open_posix_beneath(Path(root), parts, mode) as stream:
        yield stream


@_trace_safe_file_operation
def unlink_file_beneath(root: str | Path, relative_path: str) -> None:
    """Remove one file below a pinned root without following path aliases."""

    parts = PurePosixPath(relative_path).parts
    if not parts or any(
        part in {"", ".", ".."} or "/" in part or "\\" in part for part in parts
    ):
        raise SafeFileError("safe_relative_path_invalid")
    if os.name == "nt":
        _unlink_windows_beneath(Path(root), parts)
        return
    _unlink_posix_beneath(Path(root), parts)


@_trace_safe_file_operation
def publish_file_beneath(
    root: str | Path, temporary_path: str, target_path: str
) -> None:
    """Atomically publish a complete sibling file without replacing a target."""

    temporary_parts = PurePosixPath(temporary_path).parts
    target_parts = PurePosixPath(target_path).parts
    if (
        not temporary_parts
        or len(temporary_parts) != len(target_parts)
        or temporary_parts[:-1] != target_parts[:-1]
        or temporary_parts[-1] == target_parts[-1]
        or any(
            part in {"", ".", ".."} or "/" in part or "\\" in part
            for part in (*temporary_parts, *target_parts)
        )
    ):
        raise SafeFileError("safe_publish_path_invalid")
    if os.name == "nt":
        _publish_windows_beneath(Path(root), temporary_parts, target_parts)
        return
    _publish_posix_beneath(Path(root), temporary_parts, target_parts)


@_trace_safe_file_operation
def ensure_directory_beneath(root: str | Path, relative_path: str) -> Path:
    """Create each directory below a pinned root without following aliases."""

    parts = PurePosixPath(relative_path).parts
    if not parts or any(
        part in {"", ".", ".."} or "/" in part or "\\" in part for part in parts
    ):
        raise SafeFileError("safe_relative_path_invalid")
    root_path = Path(root)
    if os.name == "nt":
        _ensure_windows_beneath(root_path, parts)
    else:
        _ensure_posix_beneath(root_path, parts)
    return root_path.joinpath(*parts)


@contextmanager
def _open_posix_beneath(
    root: Path, parts: tuple[str, ...], mode: Literal["rb", "xb"]
) -> Iterator[BinaryIO]:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise SafeFileError("safe_no_follow_open_unavailable")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    absolute_root = Path(os.path.abspath(root))
    if mode == "xb":
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    else:
        file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    with ExitStack() as stack:
        if not absolute_root.is_absolute() or absolute_root.anchor != "/":
            raise SafeFileError("safe_root_not_absolute")
        current = os.open("/", directory_flags)
        stack.callback(os.close, current)
        for part in absolute_root.parts[1:]:
            next_fd = os.open(part, directory_flags, dir_fd=current)
            stack.callback(os.close, next_fd)
            current = next_fd
        for part in parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=current)
            stack.callback(os.close, next_fd)
            current = next_fd
        descriptor = os.open(parts[-1], file_flags, 0o600, dir_fd=current)
        created_file = mode == "xb"
        stream: BinaryIO | None = None
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise SafeFileError("safe_regular_file_required")
            stream = os.fdopen(descriptor, mode)
            descriptor = None
            stack.enter_context(stream)
            created_file = False
        except BaseException as exc:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as cleanup_error:
                    exc.add_note(f"descriptor cleanup failed: {cleanup_error!r}")
            elif stream is not None:
                try:
                    stream.close()
                except OSError as cleanup_error:
                    exc.add_note(f"stream cleanup failed: {cleanup_error!r}")
            if created_file:
                try:
                    os.unlink(parts[-1], dir_fd=current)
                except FileNotFoundError:
                    pass
                except OSError as cleanup_error:
                    exc.add_note(f"created artifact cleanup failed: {cleanup_error!r}")
            raise
        assert stream is not None
        yield stream


def _unlink_posix_beneath(root: Path, parts: tuple[str, ...]) -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise SafeFileError("safe_no_follow_open_unavailable")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    absolute_root = Path(os.path.abspath(root))
    if not absolute_root.is_absolute() or absolute_root.anchor != "/":
        raise SafeFileError("safe_root_not_absolute")
    with ExitStack() as stack:
        current = os.open("/", directory_flags)
        stack.callback(os.close, current)
        for part in absolute_root.parts[1:]:
            next_fd = os.open(part, directory_flags, dir_fd=current)
            stack.callback(os.close, next_fd)
            current = next_fd
        for part in parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=current)
            stack.callback(os.close, next_fd)
            current = next_fd
        try:
            os.unlink(parts[-1], dir_fd=current)
        except FileNotFoundError:
            return


def _ensure_posix_beneath(root: Path, parts: tuple[str, ...]) -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise SafeFileError("safe_no_follow_open_unavailable")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    absolute_root = Path(os.path.abspath(root))
    if not absolute_root.is_absolute() or absolute_root.anchor != "/":
        raise SafeFileError("safe_root_not_absolute")
    with ExitStack() as stack:
        current = os.open("/", directory_flags)
        stack.callback(os.close, current)
        for part in absolute_root.parts[1:]:
            next_fd = os.open(part, directory_flags, dir_fd=current)
            stack.callback(os.close, next_fd)
            current = next_fd
        for part in parts:
            try:
                next_fd = os.open(part, directory_flags, dir_fd=current)
            except FileNotFoundError:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current)
                except FileExistsError:
                    pass
                next_fd = os.open(part, directory_flags, dir_fd=current)
            stack.callback(os.close, next_fd)
            current = next_fd


def _publish_posix_beneath(
    root: Path, temporary_parts: tuple[str, ...], target_parts: tuple[str, ...]
) -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise SafeFileError("safe_no_follow_open_unavailable")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    absolute_root = Path(os.path.abspath(root))
    if not absolute_root.is_absolute() or absolute_root.anchor != "/":
        raise SafeFileError("safe_root_not_absolute")
    with ExitStack() as stack:
        current = os.open("/", directory_flags)
        stack.callback(os.close, current)
        for part in absolute_root.parts[1:]:
            next_fd = os.open(part, directory_flags, dir_fd=current)
            stack.callback(os.close, next_fd)
            current = next_fd
        for part in temporary_parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=current)
            stack.callback(os.close, next_fd)
            current = next_fd
        # A hard link creates the final name atomically and fails if it exists.
        # Both names are in the same directory and refer to the fully synced file.
        os.link(
            temporary_parts[-1],
            target_parts[-1],
            src_dir_fd=current,
            dst_dir_fd=current,
            follow_symlinks=False,
        )
        os.unlink(temporary_parts[-1], dir_fd=current)
        os.fsync(current)


if os.name == "nt":
    import msvcrt
    import ntpath
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _FILE_LIST_DIRECTORY = 0x0001
    _FILE_READ_DATA = 0x0001
    _FILE_WRITE_DATA = 0x0002
    _FILE_READ_ATTRIBUTES = 0x0080
    _DELETE = 0x00010000
    _SYNCHRONIZE = 0x00100000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _OBJ_CASE_INSENSITIVE = 0x00000040
    _OBJ_DONT_REPARSE = 0x00001000
    _FILE_OPEN = 1
    _FILE_CREATE = 2
    _FILE_DIRECTORY_FILE = 0x00000001
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_OPEN_REPARSE_POINT = 0x00200000
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _STATUS_OBJECT_NAME_COLLISION = 0xC0000035
    _FILE_DISPOSITION_INFORMATION_CLASS = 13
    _FILE_RENAME_INFORMATION_CLASS = 10

    class _UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class _IoStatusBlockUnion(ctypes.Union):
        _fields_ = [("Status", ctypes.c_long), ("Pointer", ctypes.c_void_p)]

    class _IoStatusBlock(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("u", _IoStatusBlockUnion), ("Information", ctypes.c_size_t)]

    class _FileDispositionInformation(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime_dwLowDateTime", wintypes.DWORD),
            ("ftCreationTime_dwHighDateTime", wintypes.DWORD),
            ("ftLastAccessTime_dwLowDateTime", wintypes.DWORD),
            ("ftLastAccessTime_dwHighDateTime", wintypes.DWORD),
            ("ftLastWriteTime_dwLowDateTime", wintypes.DWORD),
            ("ftLastWriteTime_dwHighDateTime", wintypes.DWORD),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _CreateFileW = _kernel32.CreateFileW
    _CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _CreateFileW.restype = wintypes.HANDLE
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL
    _GetFileInformationByHandle = _kernel32.GetFileInformationByHandle
    _GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    _GetFileInformationByHandle.restype = wintypes.BOOL
    _GetFinalPathNameByHandleW = _kernel32.GetFinalPathNameByHandleW
    _GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _NtCreateFile = _ntdll.NtCreateFile
    _NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    ]
    _NtCreateFile.restype = ctypes.c_long
    _NtSetInformationFile = _ntdll.NtSetInformationFile
    _NtSetInformationFile.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_IoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    _NtSetInformationFile.restype = ctypes.c_long

    def _win_error(operation: str) -> OSError:
        error = ctypes.get_last_error()
        return OSError(error, f"{operation} failed", ctypes.FormatError(error))

    def _close_handle(handle: int) -> None:
        if not _CloseHandle(handle):
            raise _win_error("CloseHandle")

    def _run_cleanups(
        cleanups: list[Callable[[], None]], primary_error: BaseException | None
    ) -> None:
        failures: list[OSError] = []
        for cleanup in cleanups:
            try:
                cleanup()
            except OSError as cleanup_error:
                failures.append(cleanup_error)
        if not failures:
            return
        if primary_error is not None:
            for cleanup_error in failures:
                primary_error.add_note(f"handle cleanup failed: {cleanup_error!r}")
            return
        first_error = failures[0]
        for cleanup_error in failures[1:]:
            first_error.add_note(f"additional cleanup failure: {cleanup_error!r}")
        raise first_error

    def _attributes(handle: int) -> int:
        info = _ByHandleFileInformation()
        if not _GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise _win_error("GetFileInformationByHandle")
        return int(info.dwFileAttributes)

    def _mark_delete_on_close(handle: int) -> None:
        disposition = _FileDispositionInformation(True)
        io_status = _IoStatusBlock()
        status = _NtSetInformationFile(
            handle,
            ctypes.byref(io_status),
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
            _FILE_DISPOSITION_INFORMATION_CLASS,
        )
        if status < 0:
            raise OSError(status & 0xFFFFFFFF, "NtSetInformationFile delete failed")

    def _rename_relative_handle(
        file_handle: int, parent_handle: int, target_name: str
    ) -> None:
        class _FileRenameInformation(ctypes.Structure):
            _fields_ = [
                ("ReplaceIfExists", wintypes.BOOLEAN),
                ("RootDirectory", wintypes.HANDLE),
                ("FileNameLength", wintypes.DWORD),
                ("FileName", wintypes.WCHAR * 1),
            ]

        encoded_name = target_name.encode("utf-16-le")
        buffer = ctypes.create_string_buffer(
            _FileRenameInformation.FileName.offset + len(encoded_name)
        )
        information = _FileRenameInformation.from_buffer(buffer)
        information.ReplaceIfExists = False
        information.RootDirectory = parent_handle
        information.FileNameLength = len(encoded_name)
        ctypes.memmove(
            ctypes.addressof(buffer) + _FileRenameInformation.FileName.offset,
            encoded_name,
            len(encoded_name),
        )
        io_status = _IoStatusBlock()
        status = _NtSetInformationFile(
            file_handle,
            ctypes.byref(io_status),
            ctypes.byref(buffer),
            len(buffer),
            _FILE_RENAME_INFORMATION_CLASS,
        )
        if status < 0:
            unsigned_status = status & 0xFFFFFFFF
            if unsigned_status == 0xC0000035:
                raise FileExistsError(
                    unsigned_status, "file already exists", target_name
                )
            raise OSError(unsigned_status, "NtSetInformationFile rename failed")

    def _final_path(handle: int) -> str:
        buffer = ctypes.create_unicode_buffer(32768)
        length = _GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if length == 0 or length >= len(buffer):
            raise _win_error("GetFinalPathNameByHandleW")
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return ntpath.normcase(ntpath.normpath(value))

    def _verify_handle_path(handle: int, expected: str) -> None:
        if _final_path(handle) != ntpath.normcase(ntpath.normpath(expected)):
            raise SafeFileError("safe_final_path_mismatch")

    def _create_directory_handle(path: str) -> int:
        handle = _CreateFileW(
            path,
            _FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        value = handle if isinstance(handle, int) else handle.value
        if value is None or value == _INVALID_HANDLE_VALUE:
            raise _win_error("CreateFileW directory")
        try:
            attributes = _attributes(value)
            if not attributes & _FILE_ATTRIBUTE_DIRECTORY:
                raise SafeFileError("safe_component_not_directory")
            if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise SafeFileError("safe_reparse_point_rejected")
        except BaseException as exc:
            try:
                _close_handle(value)
            except OSError as cleanup_error:
                exc.add_note(f"directory handle cleanup failed: {cleanup_error!r}")
            raise
        return value

    def _open_relative_handle(
        parent: int,
        name: str,
        *,
        mode: Literal["directory", "directory_create", "rb", "xb", "delete", "rename"],
        created_file_cleanup: Callable[[], None] | None = None,
    ) -> int:
        if mode == "xb" and created_file_cleanup is None:
            raise SafeFileError("safe_created_file_cleanup_required")
        buffer = ctypes.create_unicode_buffer(name)
        byte_length = len(name.encode("utf-16-le"))
        unicode_name = _UnicodeString(
            byte_length,
            byte_length + 2,
            ctypes.cast(buffer, wintypes.LPWSTR),
        )
        object_attributes = _ObjectAttributes(
            ctypes.sizeof(_ObjectAttributes),
            parent,
            ctypes.pointer(unicode_name),
            _OBJ_CASE_INSENSITIVE | _OBJ_DONT_REPARSE,
            None,
            None,
        )
        io_status = _IoStatusBlock()
        handle = wintypes.HANDLE()
        if mode in {"directory", "directory_create"}:
            desired_access = _FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
            share_access = _FILE_SHARE_READ | _FILE_SHARE_WRITE
            disposition = _FILE_CREATE if mode == "directory_create" else _FILE_OPEN
            create_options = (
                _FILE_DIRECTORY_FILE
                | _FILE_OPEN_REPARSE_POINT
                | _FILE_SYNCHRONOUS_IO_NONALERT
            )
            attributes = 0
        else:
            data_access = (
                _FILE_READ_DATA
                if mode == "rb"
                else _FILE_WRITE_DATA
                if mode == "xb"
                else _DELETE
            )
            desired_access = (
                data_access
                | (_DELETE if mode == "xb" else 0)
                | _FILE_READ_ATTRIBUTES
                | _SYNCHRONIZE
            )
            share_access = _FILE_SHARE_READ | _FILE_SHARE_WRITE
            disposition = _FILE_CREATE if mode == "xb" else _FILE_OPEN
            create_options = (
                _FILE_NON_DIRECTORY_FILE
                | _FILE_OPEN_REPARSE_POINT
                | _FILE_SYNCHRONOUS_IO_NONALERT
            )
            attributes = 0x80
        status = _NtCreateFile(
            ctypes.byref(handle),
            desired_access,
            ctypes.byref(object_attributes),
            ctypes.byref(io_status),
            None,
            attributes,
            share_access,
            disposition,
            create_options,
            None,
            0,
        )
        if status < 0:
            unsigned_status = status & 0xFFFFFFFF
            if unsigned_status == _STATUS_OBJECT_NAME_COLLISION:
                raise FileExistsError(unsigned_status, "file already exists", name)
            raise OSError(unsigned_status, "NtCreateFile relative open failed", name)
        value = handle.value
        try:
            file_attributes = _attributes(value)
            if file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise SafeFileError("safe_reparse_point_rejected")
            if (
                mode in {"directory", "directory_create"}
                and not file_attributes & _FILE_ATTRIBUTE_DIRECTORY
            ):
                raise SafeFileError("safe_component_not_directory")
        except BaseException as exc:
            needs_path_cleanup = False
            if mode == "xb":
                try:
                    _mark_delete_on_close(value)
                except OSError as cleanup_error:
                    exc.add_note(f"created file disposition failed: {cleanup_error!r}")
                    needs_path_cleanup = True
            try:
                _close_handle(value)
            except OSError as cleanup_error:
                exc.add_note(f"file handle cleanup failed: {cleanup_error!r}")
                needs_path_cleanup = mode == "xb"
            if needs_path_cleanup and created_file_cleanup is not None:
                try:
                    created_file_cleanup()
                except FileNotFoundError:
                    pass
                except OSError as cleanup_error:
                    exc.add_note(f"created file path cleanup failed: {cleanup_error!r}")
            raise
        return value

    @contextmanager
    def _open_windows_beneath(
        root: Path, parts: tuple[str, ...], mode: Literal["rb", "xb"]
    ) -> Iterator[BinaryIO]:
        absolute_root = Path(os.path.abspath(root))
        anchor = absolute_root.anchor
        if not anchor:
            raise SafeFileError("safe_root_not_absolute")
        handles: list[int] = []
        file_handle: int | None = None
        descriptor: int | None = None
        stream: BinaryIO | None = None
        created_file = False
        primary_error: BaseException | None = None

        def remove_created_artifact() -> None:
            nonlocal created_file
            _unlink_windows_beneath(root, parts)
            created_file = False

        try:
            current = _create_directory_handle(anchor)
            handles.append(current)
            current_path = anchor
            for part in absolute_root.parts[1:]:
                current = _open_relative_handle(current, part, mode="directory")
                handles.append(current)
                current_path = ntpath.join(current_path, part)
                _verify_handle_path(current, current_path)
            for part in parts[:-1]:
                current = _open_relative_handle(current, part, mode="directory")
                handles.append(current)
                current_path = ntpath.join(current_path, part)
                _verify_handle_path(current, current_path)
            file_handle = _open_relative_handle(
                current,
                parts[-1],
                mode=mode,
                created_file_cleanup=(
                    lambda: (
                        _unlink_windows_beneath(root, parts) if mode == "xb" else None
                    )
                ),
            )
            created_file = mode == "xb"
            current_path = ntpath.join(current_path, parts[-1])
            _verify_handle_path(file_handle, current_path)
            fd_flags = os.O_BINARY | (os.O_RDONLY if mode == "rb" else os.O_WRONLY)
            descriptor = msvcrt.open_osfhandle(file_handle, fd_flags)
            file_handle = None
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise SafeFileError("safe_regular_file_required")
            stream = os.fdopen(descriptor, mode)
            descriptor = None
            with stream as opened_stream:
                created_file = False
                yield opened_stream
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            cleanups: list[Callable[[], None]] = []
            if descriptor is not None:
                cleanups.append(lambda: os.close(descriptor))
            if stream is not None:
                cleanups.append(lambda: stream.close())
            if file_handle is not None:
                cleanups.append(lambda: _close_handle(file_handle))
            for handle in reversed(handles):
                cleanups.append(lambda handle=handle: _close_handle(handle))
            if created_file:
                cleanups.append(remove_created_artifact)
            _run_cleanups(cleanups, primary_error)

    def _unlink_windows_beneath(root: Path, parts: tuple[str, ...]) -> None:
        absolute_root = Path(os.path.abspath(root))
        anchor = absolute_root.anchor
        if not anchor:
            raise SafeFileError("safe_root_not_absolute")
        handles: list[int] = []
        file_handle: int | None = None
        primary_error: BaseException | None = None
        try:
            current = _create_directory_handle(anchor)
            handles.append(current)
            current_path = anchor
            for part in absolute_root.parts[1:]:
                current = _open_relative_handle(current, part, mode="directory")
                handles.append(current)
                current_path = ntpath.join(current_path, part)
                _verify_handle_path(current, current_path)
            for part in parts[:-1]:
                current = _open_relative_handle(current, part, mode="directory")
                handles.append(current)
                current_path = ntpath.join(current_path, part)
                _verify_handle_path(current, current_path)
            try:
                file_handle = _open_relative_handle(current, parts[-1], mode="delete")
            except OSError as exc:
                if exc.errno in {2, 0xC0000034}:
                    return
                raise
            _verify_handle_path(file_handle, ntpath.join(current_path, parts[-1]))
            disposition = _FileDispositionInformation(True)
            io_status = _IoStatusBlock()
            status = _NtSetInformationFile(
                file_handle,
                ctypes.byref(io_status),
                ctypes.byref(disposition),
                ctypes.sizeof(disposition),
                _FILE_DISPOSITION_INFORMATION_CLASS,
            )
            if status < 0:
                raise OSError(status & 0xFFFFFFFF, "NtSetInformationFile delete failed")
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            cleanups: list[Callable[[], None]] = []
            if file_handle is not None:
                cleanups.append(lambda: _close_handle(file_handle))
            for handle in reversed(handles):
                cleanups.append(lambda handle=handle: _close_handle(handle))
            _run_cleanups(cleanups, primary_error)

    def _ensure_windows_beneath(root: Path, parts: tuple[str, ...]) -> None:
        absolute_root = Path(os.path.abspath(root))
        anchor = absolute_root.anchor
        if not anchor:
            raise SafeFileError("safe_root_not_absolute")
        handles: list[int] = []
        primary_error: BaseException | None = None
        try:
            current = _create_directory_handle(anchor)
            handles.append(current)
            current_path = anchor
            for part in absolute_root.parts[1:]:
                current = _open_relative_handle(current, part, mode="directory")
                handles.append(current)
                current_path = ntpath.join(current_path, part)
                _verify_handle_path(current, current_path)
            for part in parts:
                try:
                    next_handle = _open_relative_handle(
                        current, part, mode="directory_create"
                    )
                except FileExistsError:
                    next_handle = _open_relative_handle(current, part, mode="directory")
                handles.append(next_handle)
                current = next_handle
                current_path = ntpath.join(current_path, part)
                _verify_handle_path(current, current_path)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            cleanups = [
                lambda handle=handle: _close_handle(handle)
                for handle in reversed(handles)
            ]
            _run_cleanups(cleanups, primary_error)

    def _publish_windows_beneath(
        root: Path, temporary_parts: tuple[str, ...], target_parts: tuple[str, ...]
    ) -> None:
        absolute_root = Path(os.path.abspath(root))
        anchor = absolute_root.anchor
        if not anchor:
            raise SafeFileError("safe_root_not_absolute")
        handles: list[int] = []
        file_handle: int | None = None
        primary_error: BaseException | None = None
        try:
            current = _create_directory_handle(anchor)
            handles.append(current)
            current_path = anchor
            for part in absolute_root.parts[1:]:
                current = _open_relative_handle(current, part, mode="directory")
                handles.append(current)
                current_path = ntpath.join(current_path, part)
                _verify_handle_path(current, current_path)
            for part in temporary_parts[:-1]:
                current = _open_relative_handle(current, part, mode="directory")
                handles.append(current)
                current_path = ntpath.join(current_path, part)
                _verify_handle_path(current, current_path)
            file_handle = _open_relative_handle(
                current, temporary_parts[-1], mode="rename"
            )
            _verify_handle_path(
                file_handle, ntpath.join(current_path, temporary_parts[-1])
            )
            _rename_relative_handle(file_handle, current, target_parts[-1])
            _verify_handle_path(
                file_handle, ntpath.join(current_path, target_parts[-1])
            )
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            cleanups: list[Callable[[], None]] = []
            if file_handle is not None:
                cleanups.append(lambda: _close_handle(file_handle))
            for handle in reversed(handles):
                cleanups.append(lambda handle=handle: _close_handle(handle))
            _run_cleanups(cleanups, primary_error)

else:

    @contextmanager
    def _open_windows_beneath(
        root: Path, parts: tuple[str, ...], mode: Literal["rb", "xb"]
    ) -> Iterator[BinaryIO]:
        raise SafeFileError("windows_open_unavailable")

    def _unlink_windows_beneath(root: Path, parts: tuple[str, ...]) -> None:
        raise SafeFileError("windows_open_unavailable")

    def _ensure_windows_beneath(root: Path, parts: tuple[str, ...]) -> None:
        raise SafeFileError("windows_open_unavailable")

    def _publish_windows_beneath(
        root: Path, temporary_parts: tuple[str, ...], target_parts: tuple[str, ...]
    ) -> None:
        raise SafeFileError("windows_open_unavailable")
