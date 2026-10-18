"""Serialize expensive standalone IFC readers during staged recovery."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path


def wait_for_reader(root):
    marker=Path(root)/'artifacts/runtime/ifc-reader-lease.json'
    if not marker.exists():return
    lease=json.loads(marker.read_text(encoding='utf-8'));pid=int(lease['pid'])
    if pid==os.getpid():return
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x100000,False,pid)
    if not handle:return
    try:
        print('Ожидание завершения другого IFC-обработчика: защита от одновременной загрузки двух больших IFC в память',flush=True)
        while kernel.WaitForSingleObject(handle,1000)==258:pass
    finally:kernel.CloseHandle(handle)

