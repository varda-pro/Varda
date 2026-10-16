# ruff: noqa
# Phase 0 measurement script (scratch quality, kept for reproducibility). See README.md.
import numpy as np, glob, re
exec(open('co2.py').read().split('# --- MTRDR')[0])
D='/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper/'
for obs in ['FRT00005850','FRT00005C5E','FRT000047A3','FRT000066A4','FRT0001FB74','HRL000040FF']:
    h=glob.glob(D+obs+'/mtrdr/*_if*_mtr3.hdr')[0]; t=open(h).read()
    S=int(re.search(r'samples\s*=\s*(\d+)',t).group(1)); L=int(re.search(r'lines\s*=\s*(\d+)',t).group(1)); B=int(re.search(r'bands\s*=\s*(\d+)',t).group(1))
    wl=np.array([float(v) for v in re.search(r'wavelength = \{([^}]*)\}',t).group(1).split(',')])
    cube=np.memmap(h.replace('.hdr','.img'),dtype='<f4',mode='r',shape=(B,L,S))
    sel=np.nonzero((wl>1930)&(wl<2110))[0]
    sub=np.array(cube[sel][:, ::2, ::2]).transpose(1,2,0)
    ok=np.all((sub!=65535)&(sub>0),-1)
    rms,res,w=co2metric(wl[sel],np.where(ok[...,None],sub,1.0))
    mres=np.median(res[ok],0)
    print(obs, 'pixel RMS median %.4f'%np.median(rms[ok]), 'RMS of median residual %.4f'%np.sqrt(np.mean(mres**2)))
    print('   ', ' '.join(f'{a:.0f}:{b*1000:+.1f}' for a,b in zip(w,mres)))
