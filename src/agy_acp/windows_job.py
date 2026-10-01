from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from typing import Any, ClassVar

_KILL_ON_JOB_CLOSE = 0x00002000
_EXTENDED_LIMIT_INFORMATION = 9


def attach_kill_on_close_job(process_handle: int) -> Callable[[], None]:
    if os.name != "nt":
        raise OSError("Windows Job Objects are unavailable")

    ctypes: Any = importlib.import_module("ctypes")
    wintypes: Any = importlib.import_module("ctypes.wintypes")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class BasicLimitInformation(ctypes.Structure):  # type: ignore[misc]
        _fields_: ClassVar[list[tuple[str, Any]]] = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):  # type: ignore[misc]
        _fields_: ClassVar[list[tuple[str, Any]]] = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class ExtendedLimitInformation(ctypes.Structure):  # type: ignore[misc]
        _fields_: ClassVar[list[tuple[str, Any]]] = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    create_job = kernel32.CreateJobObjectW
    create_job.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    create_job.restype = wintypes.HANDLE
    set_information = kernel32.SetInformationJobObject
    set_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    set_information.restype = wintypes.BOOL
    assign_process = kernel32.AssignProcessToJobObject
    assign_process.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    assign_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = create_job(None, None)
    if not handle:
        raise OSError("Windows Job Object creation failed")
    information = ExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
    if not set_information(
        handle,
        _EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        close_handle(handle)
        raise OSError("Windows Job Object configuration failed")
    if not assign_process(handle, wintypes.HANDLE(process_handle)):
        close_handle(handle)
        raise OSError("Windows Job Object assignment failed")

    closed = False

    def close_job() -> None:
        nonlocal closed
        if not closed:
            closed = True
            close_handle(handle)

    return close_job
