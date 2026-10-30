import sys, collections
def dom_step(s):
    c=collections.Counter((s[i+1]-s[i])&255 for i in range(len(s)-1)); return c.most_common(1)[0][0]
def rbase(s,st):
    c=collections.Counter((b-i*st)&255 for i,b in enumerate(s)); return c.most_common(1)[0][0]
def purity(s,b,st): return sum(1 for i,x in enumerate(s) if x==(b+i*st)&255)/len(s)
def analyze(path):
    d=open(path,'rb').read(); n=len(d)//4096
    K=collections.Counter(); steps=collections.defaultdict(collections.Counter); m=0
    for pn in range(1,n):
        pg=d[pn*4096:(pn+1)*4096]
        for si in range(8):
            sec=pg[si*512:(si*512+512) if si<7 else 0xFF0]
            st=dom_step(sec); b=rbase(sec,st)
            if purity(sec,b,st)<0.97: continue
            m+=1; K[(b-pn-si)&255]+=1; steps[(pn+si+1)//2][st]+=1
    k0,kc=K.most_common(1)[0]
    amb=sum(1 for c in steps.values() if len(c)>1)
    S={k:c.most_common(1)[0][0] for k,c in steps.items()}
    print(f"{path.split('/')[-1]}: pure={m} K={k0} ({kc}/{m}) other K: {K.most_common(4)[1:]}")
    print(f"  step slots learned {len(S)} of {(n+8)//2}, ambiguous {amb}, all odd: {all(v&1 for v in S.values())}")
    return k0,S,n
ka,A,n=analyze(sys.argv[1]); kb,B,_=analyze(sys.argv[2])
ks=sorted(A); print("A steps k=0..40:", [A.get(k) for k in range(41)])
print("B steps k=0..40:", [B.get(k) for k in range(41)])
com=set(A)&set(B)
for name,f in [("B-A",lambda a,b:(b-a)&255),("B*inv(A)",lambda a,b:(b*pow(a,-1,256))&255),("B^A",lambda a,b:a^b)]:
    print(name, collections.Counter(f(A[k],B[k]) for k in com).most_common(3))
# step recurrence within file A
r=collections.Counter((A[k+1]*pow(A[k],-1,256))&255 for k in A if k+1 in A); print("A ratio A[k+1]/A[k]:", r.most_common(4))
dd=collections.Counter((A[k+1]-A[k])&255 for k in A if k+1 in A); print("A diff:", dd.most_common(4))
