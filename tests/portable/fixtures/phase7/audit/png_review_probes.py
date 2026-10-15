"""Independent phase7 PNG reviewer probes, preregistered before final acceptance."""
import hashlib,json,struct,sys,zlib
from pathlib import Path
from unittest.mock import patch
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'phase5/trusted'));from common import source_digest
SOURCE=Path(sys.argv[1]).resolve();sys.path.insert(0,str(SOURCE))
from agent_workflow.uiux import validate_png

def chunk(k,b):return struct.pack('!I',len(b))+k+b+struct.pack('!I',zlib.crc32(k+b)&0xffffffff)
def make(w,h,depth,color,interlace=0,raw=None,split=False,tail=b'',header=None):
    layout=[(0,0,1,1)] if not interlace else [(0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)]
    channels={0:1,2:3,3:1,4:2,6:4}[color];pixels=bytearray()
    for x,y,dx,dy in layout:
        pw=max(0,(w-x+dx-1)//dx);ph=max(0,(h-y+dy-1)//dy)
        if pw and ph:pixels.extend((b'\0'+b'\0'*((pw*channels*depth+7)//8))*ph)
    data=zlib.compress(bytes(pixels) if raw is None else raw)
    head=chunk(b'IHDR',header or struct.pack('!IIBBBBB',w,h,depth,color,0,0,interlace))
    palette=chunk(b'PLTE',b'\0\0\0') if color==3 else b''
    encoded=chunk(b'IDAT',data[:len(data)//2])+chunk(b'IDAT',data[len(data)//2:]+tail) if split else chunk(b'IDAT',data+tail)
    return b'\x89PNG\r\n\x1a\n'+head+palette+encoded+chunk(b'IEND',b'')

def accepts(data,w=13,h=9):validate_png(data,w,h)
def rejects(data,w=13,h=9):
    try:validate_png(data,w,h)
    except ValueError:return
    raise AssertionError('invalid PNG admitted')

before=source_digest(SOURCE);results=[]
def case(label,fn):
    try:fn();results.append({'case':label,'passed':True})
    except Exception as e:results.append({'case':label,'passed':False,'error':type(e).__name__+': '+str(e)})
for color,depths in {0:[1,2,4,8,16],2:[8,16],3:[1,2,4,8],4:[8,16],6:[8,16]}.items():
    for depth in depths:
        for interlace in [0,1]:case('valid_color%d_depth%d_interlace%d'%(color,depth,interlace),lambda c=color,d=depth,i=interlace:accepts(make(13,9,d,c,i)))
case('valid_consecutive_split_IDAT',lambda:accepts(make(13,9,8,2,split=True)))
case('reject_excess_inflated_pixels',lambda:rejects(make(13,9,8,2,raw=b'\0'*70000)))
case('reject_short_pixel_stream',lambda:rejects(make(13,9,8,2,raw=b'\0')))
case('reject_trailing_compressed_bytes',lambda:rejects(make(13,9,8,2,tail=b'garbage')))
case('reject_second_zlib_stream',lambda:rejects(make(13,9,8,2,tail=zlib.compress(b'\0'))))
case('reject_invalid_filter',lambda:rejects(make(13,9,8,2,raw=(b'\x05'+b'\0'*39)*9)))
case('reject_palette_index',lambda:rejects(make(13,9,8,3,raw=(b'\0'+b'\x01'*13)*9)))
case('reject_invalid_IHDR_bit_depth',lambda:rejects(make(13,9,8,2,header=struct.pack('!IIBBBBB',13,9,4,2,0,0,0))))
case('reject_viewport_mismatch',lambda:rejects(make(13,9,8,2),w=14,h=9))
def ceiling():
    header=chunk(b'IHDR',struct.pack('!IIBBBBB',16384,16384,16,6,0,0,0))
    data=b'\x89PNG\r\n\x1a\n'+header+chunk(b'IDAT',zlib.compress(b'\0'))+chunk(b'IEND',b'')
    with patch('agent_workflow.uiux.zlib.decompressobj',side_effect=AssertionError('inflate started before decoded cap rejection')):rejects(data,w=16384,h=16384)
case('reject_oversize_before_inflate',ceiling)
after=source_digest(SOURCE);passed=all(r['passed'] for r in results) and before==after
print(json.dumps({'id':'p7-independent-png-probes','passed':passed,'exact_source_sha256':before,'source_unchanged':before==after,'evidence_kind':'native_fixture','external_provider_calls':0,'cases':results},sort_keys=True,indent=2));raise SystemExit(0 if passed else 1)
