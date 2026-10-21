# ruff: noqa
# Phase 0 measurement script (scratch quality, kept for reproducibility). See README.md.
import numpy as np, glob, re
D='/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper/'
def co2metric(wl, spec):
    """RMS of residual (spectrum / quadratic fit) over 1940-2100 nm, per spectrum. spec (..., nb)"""
    m=(wl>1940)&(wl<2100); x=(wl[m]-2020)/100
    A=np.vstack([np.ones_like(x),x,x**2]).T
    Y=spec[...,m].reshape(-1,m.sum()).T
    coef,*_=np.linalg.lstsq(A,Y,rcond=None); fit=A@coef
    r=(Y/fit-1); return np.sqrt(np.mean(r**2,0)).reshape(spec.shape[:-1]), r.T.reshape(spec.shape[:-1]+(m.sum(),)), wl[m]
# --- MTRDR
def mtrdr(obs):
    h=glob.glob(D+obs+'/mtrdr/*_if*_mtr3.hdr')[0]; t=open(h).read()
    S=int(re.search(r'samples\s*=\s*(\d+)',t).group(1)); L=int(re.search(r'lines\s*=\s*(\d+)',t).group(1)); B=int(re.search(r'bands\s*=\s*(\d+)',t).group(1))
    wl=np.array([float(v) for v in re.search(r'wavelength = \{([^}]*)\}',t).group(1).split(',')])
    cube=np.memmap(h.replace('.hdr','.img'),dtype='<f4',mode='r',shape=(B,L,S))
    sel=np.nonzero((wl>1930)&(wl<2110))[0]
    sub=np.array(cube[sel]).transpose(1,2,0)
    inn=glob.glob(D+obs+'/mtrdr/*_in*_mtr3.img')[0]
    ib=np.memmap(inn,dtype='<f4',mode='r',shape=(9,L,S)); irs=np.array(ib[5]); art=np.array(ib[2])
    ok=np.all((sub!=65535)&(sub>0),-1)&(irs!=65535)
    rms,_,_=co2metric(wl[sel],np.where(ok[...,None],sub,1.0))
    cols=irs[ok].astype(int); per=np.full(640,np.nan)
    # per-column median spectrum
    colspec=np.full((640,len(sel)),np.nan)
    for c in np.unique(cols):
        mm=ok&(irs==c)
        if mm.sum()>50: colspec[c]=np.median(sub[mm],0)
    cr,_,_=co2metric(wl[sel],np.nan_to_num(colspec,nan=1.0))
    good=~np.isnan(colspec[:,0])
    print(f'{obs}: pixel CO2-RMS median {np.median(rms[ok]):.4f} p90 {np.percentile(rms[ok],90):.4f} | per-column-median-spectrum RMS median {np.median(cr[good]):.4f} p90 {np.percentile(cr[good],90):.4f} | ATM shift artifact band median {np.median(art[art!=65535]):.4g}')
for o in ['FRT00005850','FRT00005C5E','FRT000047A3','FRT000066A4','FRT0001FB74','HRL000040FF']:
    mtrdr(o)
