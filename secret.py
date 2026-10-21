"""Windows DPAPI binds saved drive sign-ins to the current Windows user."""
import base64
import ctypes
import os


def protect_secret(value,decode=False):
    if decode:
        prefix,payload = value.split(':',1)
        raw = base64.b64decode(payload)
        if prefix=='local':
            return raw.decode()
    else:
        raw = value.encode()
    if os.name!='nt':
        if decode:
            raise ValueError('这份登录信息属于另一台 Windows，请重新登录。')
        return 'local:'+base64.b64encode(raw).decode()
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('length',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32',use_last_error=True)
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    method = crypt.CryptUnprotectData if decode else crypt.CryptProtectData
    method.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    method.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not method(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise ValueError('无法读取或保存登录信息，请在本机重新登录。')
    try:
        result = ctypes.string_at(target.data,target.length)
        return result.decode() if decode else 'dpapi:'+base64.b64encode(result).decode()
    finally:
        kernel.LocalFree(target.data)
