"""Read flat Parquet columns with explicit, checked supported encodings.
Temporary research extraction utility; implements Apache Parquet/Thrift formats.
"""
import struct, gzip

class Compact:
    def __init__(self,data,pos=0):self.data=data;self.pos=pos
    def take(self,n):
        b=self.data[self.pos:self.pos+n];self.pos+=n
        if len(b)!=n:raise EOFError
        return b
    def byte(self):return self.take(1)[0]
    def uv(self):
        n=s=0
        while True:
            b=self.byte();n|=(b&127)<<s
            if not b&128:return n
            s+=7
    def sv(self):
        n=self.uv();return (n>>1)^-(n&1)
    def val(self,t):
        if t==1:return True
        if t==2:return False
        if t==3:return struct.unpack('b',self.take(1))[0]
        if t in (4,5,6):return self.sv()
        if t==7:return struct.unpack('<d',self.take(8))[0]
        if t==8:return self.take(self.uv())
        if t in (9,10):
            h=self.byte();n=h>>4;v=h&15
            if n==15:n=self.uv()
            return [self.val(self.byte() if v in (1,2) else v) for _ in range(n)]
        if t==11:
            n=self.uv()
            if not n:return {}
            h=self.byte();return {self.val(h>>4):self.val(h&15) for _ in range(n)}
        if t==12:return self.obj()
        raise ValueError(('type',t))
    def obj(self):
        last=0;d={}
        while True:
            h=self.byte()
            if not h:return d
            delta=h>>4;fid=last+delta if delta else self.sv();last=fid
            d[fid]=self.val(h&15)

def snappy(data):
    c=Compact(data);expected=c.uv();out=bytearray()
    while c.pos<len(data):
        tag=c.byte();typ=tag&3
        if typ==0:
            n=tag>>2
            if n>=60:n=int.from_bytes(c.take(n-59),'little')
            out+=c.take(n+1)
        else:
            if typ==1:n=4+((tag>>2)&7);off=((tag&224)<<3)|c.byte()
            elif typ==2:n=1+(tag>>2);off=int.from_bytes(c.take(2),'little')
            else:n=1+(tag>>2);off=int.from_bytes(c.take(4),'little')
            assert 0<off<=len(out)
            chunk=bytes(out[-off:]);out+=(chunk*((n+off-1)//off))[:n]
    assert len(out)==expected
    return bytes(out)

def hybrid(data,width,n):
    c=Compact(data);out=[]
    while len(out)<n:
        header=c.uv()
        if header&1:
            count=(header>>1)*8;block=c.take((count*width+7)//8)
            # Decode in eight-value groups to avoid giant-integer shifts.
            for i in range(0,len(block),width or 1):
                v=int.from_bytes(block[i:i+width],'little') if width else 0
                out.extend((v>>(j*width))&((1<<width)-1) for j in range(8))
            if width==0:out.extend([0]*count)
        else:
            count=header>>1;v=int.from_bytes(c.take((width+7)//8),'little');out.extend([v]*count)
        if header==0:raise ValueError('zero RLE run')
    return out[:n]

def plain(data,typ,n):
    formats={1:('i',4),2:('q',8),4:('f',4),5:('d',8)}
    if typ in formats:
        f,s=formats[typ];assert len(data)>=s*n;return list(struct.unpack('<'+str(n)+f,data[:s*n]))
    if typ==0:return [(data[i//8]>>(i%8))&1 for i in range(n)]
    if typ==6:
        c=Compact(data);v=[]
        for _ in range(n):v.append(c.take(struct.unpack('<I',c.take(4))[0]).decode())
        return v
    raise ValueError(('plain type',typ))

def column(data,md,optional=True):
    typ=md[1];codec=md[4];out=[];dictionary=None;c=Compact(data)
    if codec not in (0,1,2):raise ValueError('Unsupported Parquet codec; refusing to decode')
    while c.pos<len(data) and len(out)<md[5]:
        h=c.obj();payload=c.take(h[3]);page_type=h[1]
        if page_type==2:
            raw=snappy(payload) if codec==1 else gzip.decompress(payload) if codec==2 else payload
            assert len(raw)==h[2]
            dictionary=plain(raw,typ,h[7][1]);continue
        if page_type not in (0,3):continue
        if page_type==0:
            ph=h[5];n=ph[1];enc=ph[2]
            raw=snappy(payload) if codec==1 else gzip.decompress(payload) if codec==2 else payload
            assert len(raw)==h[2]
            if optional:
                length=struct.unpack('<I',raw[:4])[0];levels=hybrid(raw[4:4+length],1,n);raw=raw[4+length:]
            else:levels=[1]*n
        else:
            ph=h[8];n=ph[1];enc=ph[4];dl=ph[5];rl=ph[6]
            assert rl==0
            levels=hybrid(payload[:dl],1,n) if optional else [1]*n
            raw=payload[dl:]
            if ph.get(7,True):raw=snappy(raw) if codec==1 else gzip.decompress(raw) if codec==2 else raw
        nn=sum(levels)
        if enc==0:vals=plain(raw,typ,nn)
        elif enc in (2,8):
            assert dictionary is not None
            vals=[dictionary[i] for i in hybrid(raw[1:],raw[0],nn)] if nn else []
        else:raise ValueError(('unsupported encoding',enc))
        it=iter(vals);out.extend(next(it) if l else None for l in levels)
    assert len(out)==md[5],(len(out),md[5])
    return out
