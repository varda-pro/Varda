# ruff: noqa
# Phase 0 measurement script (scratch quality, kept for reproducibility). See README.md.
import numpy as np, glob
exec(open('fit.py').read().split('def fitcol')[0])
exec(open('co2.py').read().split('# --- MTRDR')[0])
g=np.linspace(1940,2100,25)
def model(lam,y,t,terms):
    m=np.isfinite(lam)&np.isfinite(y)&(y>0)&np.isfinite(t)&(t>0)&(lam>1850)&(lam<2200)
    o=np.argsort(lam[m]); L=lam[m][o]; ly=np.log(y[m][o]); lt=np.log(t[m][o])
    x=(L-2000)/150
    cols=[np.ones_like(x),x,x**2,x**3,lt]
    if 'cog' in terms: cols.append(lt**2)
    if 'shift' in terms: cols.append(np.gradient(lt,L))
    A=np.vstack(cols).T; c,*_=np.linalg.lstsq(A,ly,rcond=None)
    atm=A[:,4:]@c[4:]; corr=np.exp(ly-atm)
    spg=np.interp(g,L,corr); r,res,_=co2metric(g,spg[None]); return float(r[0]),res[0],c
for obs in ['FRS00031442','FRS0002FE75','FRS00031260']:
    f=glob.glob(D+obs+'/trdr/*_if*l_trr3.img')[0]; L_=int(open(f.replace('.img','.lbl')).read().split('LINES')[1].split('=')[1].split()[0])
    cube=rd(f,L_,438); ddr=np.fromfile(glob.glob(D+obs+'/ddr/*_de*l_ddr1.img')[0],'<f4').reshape(14,L_,640)
    x=np.where((cube==65535)|(cube<1e-6),np.nan,cube)/np.cos(np.radians(ddr[0]))[:,None,:]
    colmed=np.nanmedian(x,0)
    line=[]
    for k in ATS:
        for terms in [(),('shift',),('cog',),('cog','shift')]:
            out=[model(wa[:,c],colmed[:,c],T[k][:,c],terms) for c in range(40,620,4)]
            R=np.array([o[1] for o in out]); common=np.median(R,0)
            line.append((k,terms,np.median([o[0] for o in out]),np.sqrt(np.mean(common**2))))
    best=sorted(line,key=lambda z:z[2])[:4]
    print(obs)
    for k in ['490947778566','420843667218']:
        print('  ',k,' '.join(f"{'+'.join(t) or 'base'}: {r:.4f}(common {c:.4f})" for kk,t,r,c in line if kk==k))
    print('   best overall:',[(b[0][:6],'+'.join(b[1]),round(b[2],4)) for b in best])
