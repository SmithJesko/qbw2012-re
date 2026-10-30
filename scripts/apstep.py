import sys, collections
exec(open(sys.argv[3]).read().split("ka,A,n=")[0])  # reuse helpers + analyze()
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    ka,A,n=analyze(sys.argv[1]); kb,B,nb=analyze(sys.argv[2])
best=[]
for s in range(-4000,4000):
    pairs=[(A[k+s],B[k]) for k in B if k+s in A]
    if len(pairs)<50: continue
    m=sum(a==b for a,b in pairs); best.append((m/len(pairs),s,len(pairs)))
best.sort(reverse=True); print("shift A[k+s]==B[k] best:", best[:4])
# Recover step for every slot using all sectors (pure or not): base known, choose step maximizing zero-plaintext count summed over the slot's sectors
def recover_all(path,K):
    d=open(path,'rb').read(); n=len(d)//4096
    score=collections.defaultdict(lambda:[0]*256)
    for pn in range(1,n):
        pg=d[pn*4096:(pn+1)*4096]
        for si in range(8):
            sec=pg[si*512:(si*512+512) if si<7 else 0xFF0]; b=(K+pn+si)&255; sc=score[(pn+si+1)//2]
            for st in range(1,256,2):
                sc[st]+=sum(1 for i,x in enumerate(sec) if x==(b+i*st)&255)
    res={}
    for k,sc in score.items():
        o=sorted(range(256),key=lambda s:-sc[s]); res[k]=(o[0],sc[o[0]],sc[o[1]])
    return res
R=recover_all(sys.argv[1],ka if len(sys.argv)<6 else int(sys.argv[5]))
agree=sum(1 for k in A if R[k][0]==A[k]); weak=sum(1 for v in R.values() if v[1]<2*max(v[2],1))
print(f"slots={len(R)} agree with pure-learned {agree}/{len(A)}; weak margin slots {weak}")
import json; json.dump({k:v[0] for k,v in R.items()}, open(sys.argv[4],'w'))
