import struct
def unpack(b, i=0):
    t = b[i]
    if t <= 0x7f: return t, i+1
    if 0x80 <= t <= 0x8f: return _map(b, i+1, t & 0x0f)
    if 0x90 <= t <= 0x9f: return _arr(b, i+1, t & 0x0f)
    if 0xa0 <= t <= 0xbf: n = t & 0x1f; return b[i+1:i+1+n].decode('utf-8','replace'), i+1+n
    if t >= 0xe0: return t - 256, i+1
    if t == 0xc0: return None, i+1
    if t == 0xc2: return False, i+1
    if t == 0xc3: return True, i+1
    if t in (0xc4,0xc5,0xc6):
        L = {0xc4:1,0xc5:2,0xc6:4}[t]; n = int.from_bytes(b[i+1:i+1+L],'big'); s=i+1+L; return bytes(b[s:s+n]), s+n
    if t in (0xc7,0xc8,0xc9):
        L = {0xc7:1,0xc8:2,0xc9:4}[t]; n = int.from_bytes(b[i+1:i+1+L],'big'); s=i+2+L; return ('ext', b[i+1+L], bytes(b[s:s+n])), s+n
    if t == 0xca: return struct.unpack('>f', b[i+1:i+5])[0], i+5
    if t == 0xcb: return struct.unpack('>d', b[i+1:i+9])[0], i+9
    if t in (0xcc,0xcd,0xce,0xcf):
        L = {0xcc:1,0xcd:2,0xce:4,0xcf:8}[t]; return int.from_bytes(b[i+1:i+1+L],'big'), i+1+L
    if t in (0xd0,0xd1,0xd2,0xd3):
        L = {0xd0:1,0xd1:2,0xd2:4,0xd3:8}[t]; return int.from_bytes(b[i+1:i+1+L],'big',signed=True), i+1+L
    if t in (0xd4,0xd5,0xd6,0xd7,0xd8):
        n = {0xd4:1,0xd5:2,0xd6:4,0xd7:8,0xd8:16}[t]; return ('ext', b[i+1], bytes(b[i+2:i+2+n])), i+2+n
    if t in (0xd9,0xda,0xdb):
        L = {0xd9:1,0xda:2,0xdb:4}[t]; n = int.from_bytes(b[i+1:i+1+L],'big'); s=i+1+L; return b[s:s+n].decode('utf-8','replace'), s+n
    if t in (0xdc,0xdd):
        L = {0xdc:2,0xdd:4}[t]; return _arr(b, i+1+L, int.from_bytes(b[i+1:i+1+L],'big'))
    if t in (0xde,0xdf):
        L = {0xde:2,0xdf:4}[t]; return _map(b, i+1+L, int.from_bytes(b[i+1:i+1+L],'big'))
    raise ValueError(hex(t))
def _arr(b,i,n):
    out=[]
    for _ in range(n): v,i=unpack(b,i); out.append(v)
    return out,i
def _map(b,i,n):
    out={}
    for _ in range(n):
        k,i=unpack(b,i); v,i=unpack(b,i); out[k if isinstance(k,(str,int)) else str(k)]=v
    return out,i
def loads(b): return unpack(b,0)[0]
