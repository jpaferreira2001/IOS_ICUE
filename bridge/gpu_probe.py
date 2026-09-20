"""Read-only probe of Gigabyte's GPU lighting library. Sets no colors."""
import ctypes
import faulthandler
import os
import sys

faulthandler.enable()
DIR = r"C:\Program Files\GIGABYTE\Control Center\Lib\GBT_VGA\GvDll"
os.add_dll_directory(DIR)
os.chdir(DIR)  # the library reads its .ini files relative to itself

print("python bitness:", ctypes.sizeof(ctypes.c_void_p) * 8)
lib = ctypes.CDLL(os.path.join(DIR, "GvLedLib.dll"))
print("loaded GvLedLib.dll")

# 1) version
lib.dllexp_GvLedGetVersion.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
lib.dllexp_GvLedGetVersion.restype = ctypes.c_uint
major, minor = ctypes.c_int(-1), ctypes.c_int(-1)
rc = lib.dllexp_GvLedGetVersion(ctypes.byref(major), ctypes.byref(minor))
print(f"GvLedGetVersion rc={rc} version={major.value}.{minor.value}")

# 2) devices
lib.dllexp_GvLedInitial.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
lib.dllexp_GvLedInitial.restype = ctypes.c_uint
count = ctypes.c_int(-1)
ids = (ctypes.c_int * 64)()
rc = lib.dllexp_GvLedInitial(ctypes.byref(count), ids)
print(f"GvLedInitial rc={rc} deviceCount={count.value} ids={[hex(i) for i in ids[:max(count.value, 0)]]}")

# 3) model name
lib.dllexp_GvLedGetVgaModelName.argtypes = [ctypes.c_char_p]
lib.dllexp_GvLedGetVgaModelName.restype = ctypes.c_uint
buf = ctypes.create_string_buffer(512)
rc = lib.dllexp_GvLedGetVgaModelName(buf)
print(f"GvLedGetVgaModelName rc={rc} name={buf.value!r}")

# clean up
lib.dllexp_GvLedDeInitial.restype = ctypes.c_uint
print("GvLedDeInitial rc=", lib.dllexp_GvLedDeInitial())
sys.stdout.flush()
