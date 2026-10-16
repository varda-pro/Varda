# ruff: noqa
# Phase 0 measurement script (scratch quality, kept for reproducibility). See README.md.
import numpy as np, glob
from scipy.ndimage import median_filter
D='/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper/'
wa=np.load('frs1442_wa.npy'); med=np.nanmedian(np.where(wa==65535,np.nan,wa)[:,30:630],1)
for obs in ['FRS00031442','FRS0002FE75','FRS00031260']:
    f=glob.glob(D+obs+'/trdr/*_if*l_trr3.img')[0]; L=int(open(f.replace('.img','.lbl')).read().split('LINES')[1].split('=')[1].split()[0])
    c=np.fromfile(f,'<f4',count=L*438*640).reshape(L,438,640)[:,:,31:631].astype(float)
    c[(c==65535)|(c<1e-6)]=np.nan
    out=[]
    for wl in (1300,1800,2300,2500):
        b=np.nanargmin(abs(med-wl)); img=c[:,b,:]
        colprof=np.nanmedian(img,0); smooth=median_filter(colprof,15,mode='nearest')
        stripe=np.nanstd(colprof/smooth-1)
        mf=median_filter(np.nan_to_num(img,nan=np.nanmedian(img)),3); d=img-mf; s=1.4826*np.nanmedian(np.abs(d))
        out.append(f'{wl}nm stripe {stripe:.4f} spikes {np.nanmean(np.abs(d)>6*s)*1e4:.1f}')
    sel=(med>1000)&(med<2600); sub=c[:,sel,:]
    mf=median_filter(np.nan_to_num(sub,nan=0),size=(1,5,1)); d=sub-mf; s=1.4826*np.nanmedian(np.abs(d),axis=(0,2),keepdims=True)
    print(obs,' | '.join(out),'| spectral spikes>6sig %.2f per 1e4'%(np.nanmean(np.abs(d)>6*s)*1e4))
