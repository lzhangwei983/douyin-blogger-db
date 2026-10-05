"""Shared local ASR with usable GPU probing and an explicit CPU fallback."""
import os,threading
from pathlib import Path
from _paths import MODELS_DIR,read_public_config
_local=threading.local()
_dll_handles=[];_dll_modules=[]

def options():
    cfg=read_public_config().get('whisper') or {}
    out={'num_workers':4,'beam_size':1,'concurrency':1,'sleep_min':1,'sleep_max':3,'device':'auto',**cfg}
    out['concurrency']=max(1,min(2,int(out['concurrency'])))
    out['sleep_max']=max(out['sleep_min'],out['sleep_max'])
    return out

def cuda_ready():
    try:
        import ctranslate2
        if os.name=='nt':
            import ctypes
            roots=[]
            envdir=os.getenv('DYDB_CUDA_DLL_DIR')
            if envdir:roots.insert(0,envdir)
            for raw in dict.fromkeys(roots):
                p=Path(raw)
                if not (p/'cublas64_12.dll').is_file() or not (p/'cudnn64_9.dll').is_file():continue
                _dll_handles.append(os.add_dll_directory(str(p)))
                _dll_modules.extend((ctypes.WinDLL(str(p/'cublas64_12.dll')),ctypes.WinDLL(str(p/'cudnn64_9.dll'))))
                if ctranslate2.get_cuda_device_count()>0:return True
            return ctranslate2.get_cuda_device_count()>0
        elif ctranslate2.get_cuda_device_count()>0:return True
        return False
    except Exception:return False

def model_path(name):
    if Path(name).is_dir():return name
    cache=MODELS_DIR/f'models--Systran--faster-whisper-{name}'
    snapshots=sorted((cache/'snapshots').glob('*'))
    if snapshots:return str(snapshots[-1])
    return name

def get_model(name='small',device=None,num_workers=None):
    from faster_whisper import WhisperModel
    cfg=options();device=device or cfg.get('device','auto');workers=num_workers or cfg['num_workers']
    if device=='auto':device='cuda' if cuda_ready() else 'cpu'
    key=(name,device,workers)
    if getattr(_local,'key',None)!=key:
        MODELS_DIR.mkdir(parents=True,exist_ok=True)
        _local.device=device
        _local.model=WhisperModel(model_path(name),device=device,compute_type='int8',download_root=str(MODELS_DIR),num_workers=workers)
        _local.key=key;_local.device=device
    return _local.model

def transcribe(path,name='small',language=None):
    cfg=options();fallback=False
    try:
        m=get_model(name);segments,info=m.transcribe(str(path),language=language,vad_filter=True,beam_size=cfg['beam_size'],condition_on_previous_text=True,temperature=[0.0]);segments=list(segments)
    except Exception:
        if getattr(_local,'device','cpu')=='cpu':raise
        fallback=True;m=get_model(name,device='cpu');segments,info=m.transcribe(str(path),language=language,vad_filter=True,beam_size=cfg['beam_size'],condition_on_previous_text=True,temperature=[0.0]);segments=list(segments)
    return ''.join(s.text for s in segments).strip(),info.language,{'device':_local.device,'fallback':fallback,'segments':[{'start':s.start,'end':s.end,'text':s.text} for s in segments]}
