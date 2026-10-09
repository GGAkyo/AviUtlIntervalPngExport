"""Create a separate text_editor.aua copy with display-only word wrapping."""
import argparse
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import shutil
import struct

k = c.WinDLL('kernel32', use_last_error=True)
u = c.WinDLL('user32', use_last_error=True)
k.LoadLibraryExW.argtypes=[w.LPCWSTR,w.HANDLE,w.DWORD]; k.LoadLibraryExW.restype=w.HMODULE
k.FindResourceW.argtypes=[w.HMODULE,c.c_void_p,c.c_void_p]; k.FindResourceW.restype=w.HANDLE
k.SizeofResource.argtypes=[w.HMODULE,w.HANDLE]; k.SizeofResource.restype=w.DWORD
k.LoadResource.argtypes=[w.HMODULE,w.HANDLE]; k.LoadResource.restype=w.HANDLE
k.LockResource.argtypes=[w.HANDLE]; k.LockResource.restype=c.c_void_p
k.FreeLibrary.argtypes=[w.HMODULE]
k.BeginUpdateResourceW.argtypes=[w.LPCWSTR,w.BOOL]; k.BeginUpdateResourceW.restype=w.HANDLE
k.UpdateResourceW.argtypes=[w.HANDLE,c.c_void_p,c.c_void_p,w.WORD,c.c_void_p,w.DWORD]; k.UpdateResourceW.restype=w.BOOL
k.EndUpdateResourceW.argtypes=[w.HANDLE,w.BOOL]; k.EndUpdateResourceW.restype=w.BOOL
NAMES=c.WINFUNCTYPE(w.BOOL,w.HMODULE,c.c_void_p,c.c_void_p,w.LPARAM)
LANGS=c.WINFUNCTYPE(w.BOOL,w.HMODULE,c.c_void_p,c.c_void_p,w.WORD,w.LPARAM)
k.EnumResourceNamesW.argtypes=[w.HMODULE,c.c_void_p,NAMES,w.LPARAM]
k.EnumResourceLanguagesW.argtypes=[w.HMODULE,c.c_void_p,c.c_void_p,LANGS,w.LPARAM]
k.FindResourceExW.argtypes=[w.HMODULE,c.c_void_p,c.c_void_p,w.WORD]; k.FindResourceExW.restype=w.HANDLE

def variable(data, offset):
    first=struct.unpack_from('<H',data,offset)[0]
    if first == 0xffff:
        return offset+4, struct.unpack_from('<H',data,offset+2)[0]
    end=offset
    while struct.unpack_from('<H',data,end)[0]: end+=2
    return end+2, bytes(data[offset:end]).decode('utf-16-le')

def wrap_dialog(raw):
    data=bytearray(raw)
    extended=struct.unpack_from('<HH',data)==(1,0xffff)
    style=struct.unpack_from('<I',data,12 if extended else 0)[0]
    count=struct.unpack_from('<H',data,16 if extended else 8)[0]
    pos=26 if extended else 18
    for _ in range(3): pos,_=variable(data,pos)
    if style & 0x40:
        pos += 6 if extended else 2
        pos,_=variable(data,pos)
    changed=[]
    for _ in range(count):
        pos=(pos+3)&~3
        style_offset=pos+8 if extended else pos
        control_style=struct.unpack_from('<I',data,style_offset)[0]
        pos += 24 if extended else 18
        pos,klass=variable(data,pos)
        pos,_=variable(data,pos)
        extra=struct.unpack_from('<H',data,pos)[0]; pos+=2+extra
        if klass in (0x81,'EDIT','Edit') and control_style & 4:
            wrapped=control_style & ~(0x80|0x100000)
            if wrapped != control_style:
                struct.pack_into('<I',data,style_offset,wrapped)
                changed.append(wrapped)
    return bytes(data), changed

def patch(source, output):
    source,output=Path(source).resolve(),Path(output).resolve()
    if source==output or output.exists(): raise ValueError('Use a new output file; the original is never edited.')
    module=k.LoadLibraryExW(str(source),None,2)
    if not module: raise c.WinError(c.get_last_error())
    resources=[]
    @NAMES
    def collect(module,kind,name,param):
        identifier=name if name<=0xffff else c.wstring_at(name)
        @LANGS
        def language(module,kind,name,lang,param):
            handle=k.FindResourceExW(module,kind,name,lang)
            size=k.SizeofResource(module,handle)
            pointer=k.LockResource(k.LoadResource(module,handle))
            raw=c.string_at(pointer,size)
            patched,styles=wrap_dialog(raw)
            if styles: resources.append((identifier,lang,patched,styles))
            return True
        k.EnumResourceLanguagesW(module,kind,name,language,0)
        return True
    try: k.EnumResourceNamesW(module,5,collect,0)
    finally: k.FreeLibrary(module)
    if sum(len(item[3]) for item in resources)!=1:
        raise ValueError('Expected exactly one multiline text editor; refusing an unknown DLL layout.')
    output.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,output)
    update=k.BeginUpdateResourceW(str(output),False)
    if not update: raise c.WinError(c.get_last_error())
    try:
        for name,lang,raw,styles in resources:
            name_buffer=c.create_unicode_buffer(name) if isinstance(name,str) else None
            pointer=c.cast(name_buffer,c.c_void_p) if name_buffer else c.c_void_p(name)
            buffer=c.create_string_buffer(raw)
            if not k.UpdateResourceW(update,5,pointer,lang,buffer,len(raw)):
                raise c.WinError(c.get_last_error())
        if not k.EndUpdateResourceW(update,False): raise c.WinError(c.get_last_error())
        update=None
    finally:
        if update: k.EndUpdateResourceW(update,True)
    verify_wrap(resources[0][3][0])
    print('Created:',output)

def verify_wrap(style):
    u.CreateWindowExW.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,c.c_int,c.c_int,c.c_int,c.c_int,w.HWND,w.HMENU,w.HINSTANCE,c.c_void_p]
    u.CreateWindowExW.restype=w.HWND
    u.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]; u.SendMessageW.restype=w.LPARAM
    u.DestroyWindow.argtypes=[w.HWND]
    parent=u.CreateWindowExW(0,'STATIC','',0,0,0,300,300,None,None,None,None)
    edit=u.CreateWindowExW(0,'EDIT','',style|0x40000000,0,0,180,200,parent,None,None,None)
    if not edit: raise c.WinError(c.get_last_error())
    try:
        text='This long paragraph should wrap without inserting any newline. '*8
        buffer=c.create_unicode_buffer(text)
        u.SendMessageW(edit,0xC,0,c.addressof(buffer))
        lines=u.SendMessageW(edit,0xBA,0,0)
        result=c.create_unicode_buffer(len(text)+1)
        u.SendMessageW(edit,0xD,len(result),c.addressof(result))
        if lines<=1 or result.value!=text: raise ValueError('Wrapping or unchanged-text verification failed.')
        print('Display wrapping verified:',lines,'lines; original text unchanged.')
    finally:
        u.DestroyWindow(edit); u.DestroyWindow(parent)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path); parser.add_argument('output',type=Path)
    args=parser.parse_args(); patch(args.source,args.output)
