#!/usr/bin/env python3
"""End-to-end synthetic IQ fixtures; no physical SDR or RF TX.
Independent transmitter formulation from upstream EPFL modulation/interleaving.
"""
import json
import base64
import pathlib
import re
import subprocess
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parent
BIN = ROOT / 'build/meshcore-epfl-rx'
N = 256
words = [0,23,45,58,78,89,99,116,139,156,166,177,197,210,232,255]
whitening = [int(x,16) for x in re.findall(r'0x([0-9A-Fa-f]+)', (ROOT/'../lib/tables.h').read_text())][:255]

def crc16(data):
    crc=0
    for v in data:
        crc ^= v << 8
        for _ in range(8): crc=((crc<<1) ^ (0x1021 if crc&0x8000 else 0))&65535
    return crc

def header(data, cr=8):
    h=[len(data)>>4,len(data)&15,((cr-4)<<1)|1]
    b=lambda i,n:(h[i]>>n)&1
    cs=[b(0,3)^b(0,2)^b(0,1)^b(0,0),
        b(0,3)^b(1,3)^b(1,2)^b(1,1)^b(2,0),
        b(0,2)^b(1,3)^b(1,0)^b(2,3)^b(2,1),
        b(0,1)^b(1,2)^b(1,0)^b(2,2)^b(2,1)^b(2,0),
        b(0,0)^b(1,1)^b(2,3)^b(2,2)^b(2,1)^b(2,0)]
    c=sum(v<<(4-i) for i,v in enumerate(cs))
    return h+[c>>4,c&15]

def symbols(data, corrupt=False, cr=8):
    c=crc16(data[:-2]) ^ data[-1] ^ (data[-2]<<8)
    if corrupt:c ^= 0x4000
    encoded=[v^whitening[i] for i,v in enumerate(data)]+[c&255,c>>8]
    nib=header(data,cr)+[n for v in encoded for n in [v&15,v>>4]]
    out=[]
    for start in [0]+list(range(6,len(nib),8)):
        sf=6 if start==0 else 8
        length=8 if start==0 else cr
        chunk=(nib[start:start+sf]+[0]*sf)[:sf]
        code=[]
        for v in chunk:
            rev=int(f'{v:04b}'[::-1],2)
            code.append(words[rev]>>(8-length))
        for i in range(length):
            g=0
            for j in range(sf):g=(g<<1)|((code[(i-j-1)%sf]>>(length-1-i))&1)
            binary=g
            for sh in range(1,8):binary ^= g>>sh
            out.append((binary*(4 if start==0 else 1)+1)%256)
    return out

def chirp(rate, ident=0, down=False):
    os=rate/62500
    n=np.arange(round(N*os),dtype=float)
    phase=n*n/(2*N*os*os)+(ident/N-.5)*n/os
    phase=np.where(n<(N-ident)*os,phase,phase-n/os)
    return np.exp(((-1 if down else 1)*2j*np.pi)*phase)

def fixture(rate,offset=0,cfo=480,corrupt=False,cr=8):
    data=b'meshcoreSDR EPFL receiver fixture; no RF TX.'
    segments=[(0,False,1)]*32+[(8,False,1),(16,False,1),(0,True,1),(0,True,1),(0,True,.25)]+[(v,False,1) for v in symbols(data,corrupt,cr)]
    parts=[]
    cursor=0.0
    os=rate/62500
    for ident,down,duration in segments:
        end=cursor+N*os*duration
        n=(np.arange(int(np.ceil(cursor)),int(np.ceil(end)))-cursor)/os
        phase=n*n/(2*N)+(ident/N-.5)*n
        phase=np.where(n<N-ident,phase,phase-n)
        parts.append(np.exp(((-1 if down else 1)*2j*np.pi)*phase))
        cursor=end
    frame=np.concatenate(parts)
    iq=np.concatenate([np.zeros(173,dtype=complex),frame,np.zeros(round(rate*.1),dtype=complex)])
    iq *= np.exp(2j*np.pi*(offset+cfo)*np.arange(len(iq))/rate)
    return data,iq.astype('<c8').tobytes()

def run(rate,corrupt=False,offset=0,cr=8):
    expected,iq=fixture(rate,offset=offset,corrupt=corrupt,cr=cr)
    p=subprocess.run([str(BIN),'--sample-rate',str(rate),'--center-hz','923250000','--frequency-hz',str(923250000+offset)],input=iq,capture_output=True,check=True)
    rows=[json.loads(s) for s in p.stdout.splitlines()]
    assert rows,(rate,corrupt,cr,p.stderr)
    assert any(r['payload_hex']==expected.hex() and r['crc']==(not corrupt) for r in rows),(rate,corrupt,cr,rows)
    print(f'PASS rate={rate} offset={offset} cr={cr} corrupt={corrupt}')

if __name__=='__main__':
    for rate in (500000,1024000):
        run(rate,offset=-125000)
        run(rate,corrupt=True,offset=-125000)
    for cr in (6,7): run(500000,cr=cr)
    p=subprocess.run([str(BIN)],input=b'bad',capture_output=True)
    assert p.returncode!=0
    print('PASS truncated sample rejection')

    import tempfile
    lorarx=pathlib.Path('/opt/meshcoresdr/lorarx/bin/lorarx')
    if lorarx.exists():
        for rate in (500000,1024000):
            expected,iq=fixture(rate,offset=-125000)
            with tempfile.TemporaryDirectory() as d:
                subprocess.run(['python3',str(ROOT/'compare.py'),'--sample-rate',str(rate),'--output-dir',d],input=iq,capture_output=True,check=True,timeout=60)
                for name in ('lorarx','epfl'):
                    rows=[json.loads(l) for l in (pathlib.Path(d)/(name+'.jsonl')).read_text().splitlines()]
                    assert any(r.get('crc')==1 and (base64.b64decode(r.get('payload',''))==expected if name=='lorarx' else r.get('payload_hex')==expected.hex()) for r in rows),(rate,name,rows)
                    if name=='lorarx':
                        assert any(r.get('crc')==1 and r.get('net')==18 for r in rows),rows
                print(f'PASS identical IQ fanout to both decoders rate={rate}')
