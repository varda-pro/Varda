# ruff: noqa
# Phase 0 measurement script (scratch quality, kept for reproducibility). See README.md.
import numpy as np, glob
exec(open('fit.py').read().split('for obs in')[0])
exec(open('co2.py').read().split('# --- MTRDR')[0])
def corrcol(lam,y,t,d,beta_mode):
    m0=np.isfinite(lam)&np.isfinite(t)&(t>0); o=np.argsort(lam[m0])
    lt=np.interp(lam+d, lam[m0][o], np.log(t[m0])[o])
    return lt
resids={}
for obs in ['FRS00031442','FRS0002FE75','FRS00031260']:
    f=glob.glob(D+obs+'/trdr/*_if*l_trr3.img')[0]; L=int(open(f.replace('.img','.lbl')).read().split('LINES')[1].split('=')[1].split()[0])
    cube=rd(f,L,438); ddr=np.fromfile(glob.glob(D+obs+'/ddr/*_de*l_ddr1.img')[0],'<f4').reshape(14,L,640)
    x=np.where((cube==65535)|(cube<1e-6),np.nan,cube)/np.cos(np.radians(ddr[0]))[:,None,:]
    colmed=np.nanmedian(x,0)
    rows={'classic-4909':[], 'classic-bestAT':[], 'fit-bestAT+shift':[]}
    # best AT by fitted metric (no shift)
    sc={k:np.median([fitcol(wa[:,c],colmed[:,c],T[k][:,c],[0.0])[0] for c in range(40,620,8)]) for k in ATS}; kb=min(sc,key=sc.get)
    R=[]
    for c in range(40,620,2):
        lam=wa[:,c]; y=colmed[:,c]
        for name,k,fitshift in [('classic-4909','490947778566',False),('classic-bestAT',kb,False),('fit-bestAT+shift',kb,True)]:
            t=T[k][:,c]
            if fitshift:
                rms,b,d=fitcol(lam,y,t,np.arange(-2,2.01,0.25)); lt=corrcol(lam,y,t,d,0); beta=-b
            else:
                i1=np.nanargmin(np.abs(np.nan_to_num(lam,nan=1e9)-1980)); i2=np.nanargmin(np.abs(np.nan_to_num(lam,nan=1e9)-2007))
                beta=np.log(y[i2]/y[i1])/np.log(t[i2]/t[i1]); lt=np.log(t)
            corr=y/np.exp(beta*lt)
            ok=np.isfinite(lam)&np.isfinite(corr)
            o=np.argsort(lam[ok]); wl=lam[ok][o]; sp=corr[ok][o]
            g=np.linspace(1940,2100,25); spg=np.interp(g,wl,sp)
            r,res,_=co2metric(g,spg[None]); rows[name].append(float(r[0]))
            if name=='fit-bestAT+shift': R.append(res[0])
    print(obs,'bestAT',kb, ' '.join(f'{k}: med {np.median(v):.4f} p90 {np.percentile(v,90):.4f}' for k,v in rows.items()))
    R=np.array(R); common=np.median(R,0); after=R-common
    print('   common residual RMS %.4f ; per-column RMS after removing common spectrum: med %.4f'%(np.sqrt(np.mean(common**2)), np.median(np.sqrt(np.mean(after**2,1)))))
    resids[obs]=common
ks=list(resids); 
for i in range(3):
    for j in range(i+1,3): print('corr of common residual',ks[i],ks[j],'%.2f'%np.corrcoef(resids[ks[i]],resids[ks[j]])[0,1])
