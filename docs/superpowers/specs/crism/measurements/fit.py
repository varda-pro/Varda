# ruff: noqa
# Phase 0 measurement script (scratch quality, kept for reproducibility). See README.md.
import numpy as np, glob
D='/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper/'; C=D+'ATO0004EC45/CRISM_CDR/'
def rd(path,L,B,S=640): return np.fromfile(path,dtype='<f4',count=L*B*S).reshape(L,B,S)
wa=np.load('frs1442_wa.npy'); wa=np.where(wa==65535,np.nan,wa)
ATS=['420843667218','420862848019','420865293319','430880416919','440914248820','490947778566']
T={k:np.where((t:=rd(glob.glob(C+'at/cdr'+k+'*.img')[0],1,438)[0])==65535,np.nan,t) for k in ATS}
win=(1850,2200)
def fitcol(lam, y, t, shifts):
    """log y = poly2(lam) - beta*log T(lam+d).  returns best (rms,beta,d)"""
    best=None
    m0=np.isfinite(y)&(y>0)&np.isfinite(t)&(t>0)&(lam>win[0])&(lam<win[1])
    o=np.argsort(lam[np.isfinite(lam)]); 
    for d in shifts:
        o=np.argsort(lam[m0]); tt=np.interp(lam+d, lam[m0][o], np.log(t[m0])[o])
        m=m0
        x=(lam[m]-2000)/150
        A=np.vstack([np.ones_like(x),x,x**2,x**3,-tt[m]]).T
        c,*_=np.linalg.lstsq(A,np.log(y[m]),rcond=None)
        r=np.log(y[m])-A@c
        core=(lam[m]>1940)&(lam[m]<2100)
        rms=np.sqrt(np.mean(r[core]**2))
        if best is None or rms<best[0]: best=(rms,c[-1],d)
    return best
for obs in ['FRS00031442','FRS0002FE75','FRS00031260']:
    f=glob.glob(D+obs+'/trdr/*_if*l_trr3.img')[0]; L=int(open(f.replace('.img','.lbl')).read().split('LINES')[1].split('=')[1].split()[0])
    cube=rd(f,L,438); ddr=np.fromfile(glob.glob(D+obs+'/ddr/*_de*l_ddr1.img')[0],'<f4').reshape(14,L,640)
    x=np.where((cube==65535)|(cube<1e-6),np.nan,cube)/np.cos(np.radians(ddr[0]))[:,None,:]
    colmed=np.nanmedian(x,0)  # (438,640)
    res={}
    for k in ATS:
        out=[fitcol(wa[:,c],colmed[:,c],T[k][:,c],[0.0]) for c in range(40,620,4)]
        res[k]=np.median([o[0] for o in out])
    kbest=min(res,key=res.get)
    outs=[fitcol(wa[:,c],colmed[:,c],T[kbest][:,c],np.arange(-3,3.01,0.25)) for c in range(40,620,4)]
    print(obs,'per-AT colRMS(no shift):',{k[:6]:round(v,4) for k,v in res.items()},'best',kbest)
    print('   with shift fit: colRMS median %.4f p90 %.4f  beta med %.2f  shift med %.2f nm IQR %s'%(np.median([o[0] for o in outs]),np.percentile([o[0] for o in outs],90),np.median([o[1] for o in outs]),np.median([o[2] for o in outs]),np.percentile([o[2] for o in outs],[25,75])))
