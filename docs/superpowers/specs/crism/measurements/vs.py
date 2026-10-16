# ruff: noqa
# Phase 0 measurement script (scratch quality, kept for reproducibility). See README.md.
import numpy as np, glob
D='/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper/'; C=D+'ATO0004EC45/CRISM_CDR/'
exec(open('co2.py').read().split('# --- MTRDR')[0])
def rd(path,L,B,S=640): return np.fromfile(path,dtype='<f4',count=L*B*S).reshape(L,B,S)
wa=np.load('frs1442_wa.npy')  # (438,640)
def run(obs, tag):
    f=glob.glob(D+obs+'/trdr/*_if*l_trr3.img')[0]; L=int(open(f.replace('.img','.lbl')).read().split('LINES')[1].split('=')[1].split()[0])
    cube=rd(f,L,438); dd=glob.glob(D+obs+'/ddr/*_de*l_ddr1.img')[0]
    ddr=np.fromfile(dd,dtype='<f4').reshape(14,L,640)
    inc=ddr[0]; cosi=np.cos(np.radians(inc))
    bad=(cube==65535)|(cube<1e-6)
    x=np.where(bad,np.nan,cube)/cosi[:,None,:]
    out={}
    for atf in ['cdr490947778566_at0000000l_7','cdr460929030422_at0000000l_7']:
        T=rd(C+'at/'+atf+'.img',1,438)[0]   # (438,640)
        T=np.where(T==65535,np.nan,T)
        # per column, band index nearest 1980 and 2007 using TRR3 WA
        i1=np.nanargmin(np.abs(np.nan_to_num(wa,nan=1e9)-1980),0); i2=np.nanargmin(np.abs(np.nan_to_num(wa,nan=1e9)-2007),0)
        cols=np.arange(640)
        T1,T2=T[i1,cols],T[i2,cols]
        R1=x[:,i1,cols]; R2=x[:,i2,cols]   # (L,640)
        beta=np.log(R2/R1)/np.log(T2/T1)
        corr=x/np.power(T[None],beta[:,None,:])
        out[atf]=(corr,beta)
    # metrics in sensor space; per-column wavelengths -> use median WA (smile ignored for metric)
    wl=np.nanmedian(np.where(wa==65535,np.nan,wa)[:,30:630],1)
    sel=np.nonzero((wl>1930)&(wl<2110))[0]
    def m(arr):
        sub=arr[:,sel,30:630].transpose(0,2,1)
        ok=np.all(np.isfinite(sub)&(sub>0),-1)
        rms,res,w=co2metric(wl[sel],np.where(ok[...,None],sub,1.0))
        colmed=np.nanmedian(np.where(ok[...,None],res,np.nan),0)  # (600, nb)
        colrms=np.sqrt(np.nanmean(colmed**2,1))
        colrms=colrms[np.isfinite(colrms)]; return np.median(rms[ok]), np.median(colrms), np.percentile(colrms,90)
    print(obs,'incidence median %.1f'%np.median(inc[inc<90]))
    print('  I/F/cos(i) only      pixRMS %.4f colRMS med %.4f p90 %.4f'%m(x))
    for k,(c,b) in out.items(): print('  classic',k[3:15],' pixRMS %.4f colRMS med %.4f p90 %.4f'%m(c), 'beta median %.2f'%np.nanmedian(b))
for o in ['FRS00031442','FRS0002FE75','FRS00031260']: run(o,'')
