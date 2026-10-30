import sys, collections
def dom_step(s):
    c=collections.Counter((s[i+1]-s[i])&255 for i in range(len(s)-1)); return c.most_common(1)[0][0]
def rbase(s,st):
    c=collections.Counter((b-i*st)&255 for i,b in enumerate(s)); return c.most_common(1)[0][0]
def purity(s,b,st): return sum(1 for i,x in enumerate(s) if x==(b+i*st)&255)/len(s)
def table(path, thresh):
    d=open(path,'rb').read(); n=len(d)//4096; out={}
    for pn in range(1,n):
        pg=d[pn*4096:(pn+1)*4096]
        for si in range(8):
            sec=pg[si*512:(si*512+512) if si<7 else 0xFF0]
            st=dom_step(sec); b=rbase(sec,st)
            if purity(sec,b,st)>=thresh: out[(pn,si)]=(b,st,pg[0xFF2])
    return out
A=table(sys.argv[1], 0.97); B=table(sys.argv[2], 0.97)
print("pure-ish sectors:", len(A), len(B))
common=sorted(set(A)&set(B))
print("common:", len(common), " same step:", sum(A[k][1]==B[k][1] for k in common), " base diff hist:", collections.Counter((A[k][0]-B[k][0])&255 for k in common).most_common(5))
print("pn si | base step type | base-pn-si  step")
for k in sorted(A)[:70]:
    b,st,t=A[k]; pn,si=k
    print(f"{pn:5} {si} | {b:3} {st:3} {t:#04x} | {(b-pn-si)&255:3} {(b-pn)&255:3}  step-?: {(st)&255:3} {(st - 2*si)&255:3}")
